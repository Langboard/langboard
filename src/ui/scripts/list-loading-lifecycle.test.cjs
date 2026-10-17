const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

// Execute the actual hooks with controlled React state and HTTP boundaries.
// This checks request/cursor lifecycles, not DOM rendering or deployed acceptance.
function harness() {
    const slots = [],
        callbacks = [],
        calls = [];
    let cursor = 0;
    const slot = (initial) => {
        const index = cursor++;
        if (!(index in slots)) slots[index] = initial;
        return [
            slots[index],
            (value) => {
                slots[index] = typeof value === "function" ? value(slots[index]) : value;
            },
        ];
    };
    const react = {
        useState: slot,
        useRef: (value) => slot({ current: value })[0],
        useCallback: (fn, deps) => {
            const index = cursor++;
            const previous = slots[index];
            if (!previous || !deps || deps.length !== previous.deps?.length || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
                slots[index] = { fn, deps };
            }
            callbacks.push(slots[index].fn);
            return slots[index].fn;
        },
        useMemo: (fn) => fn(),
        useEffect: () => {},
        createContext: () => ({ Provider: "context" }),
    };
    const model = { fromArray() {}, fromOne() {} };
    const models = new Proxy({}, { get: () => ({ Model: model }) });
    const full = [{ uid: "one" }, { uid: "two" }];
    let respond = async () => ({ data: { records: full, logs: full, schedules: full, checkitems: full, cards: full, projects: [] } });
    const api = {
        get: (url, options) => {
            calls.push({ url, ...options.params });
            return respond(url, options);
        },
    };
    const modules = new Map();
    function load(path) {
        if (modules.has(path)) return modules.get(path);
        const filename = resolve(__dirname, "../src", path);
        const code = ts.transpileModule(readFileSync(filename, "utf8"), {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
        }).outputText;
        const exports = {};
        modules.set(path, exports);
        const requireModule = (name) => {
            if (name === "react") return react;
            if (name === "react/jsx-runtime") return { jsx: (_, props) => props.value };
            if (name === "@/core/helpers/Api") return { api };
            if (name === "@/core/helpers/QueryMutation") return { useQueryMutation: () => ({ mutate: (_, fn) => ({ mutateAsync: fn }) }) };
            if (name === "@/controllers/api/shared/utils") return { getListRequestData: (form) => [model, form.testUrl || "/list"] };
            if (name === "@/core/models") return models;
            if (name === "@langboard/core/utils") return { Utils: { String: { format: () => "/bot", Token: { shortUUID: () => "list" } } } };
            if (name === "@langboard/core/constants")
                return {
                    Routing: {
                        API: {
                            DASHBOARD: { CARDS: "/cards", TRACKING: "/tracking" },
                            BOT: { LOG: { GET_ALL_BY_PROJECT: "/logs" }, SCHEDULE: { GET_ALL_BY_PROJECT: "/schedules" } },
                        },
                    },
                };
            if (name === "@/core/constants/BotRelatedConstants") return { BOT_TARGET_MODELS: models, BOT_SCHEDULES: models };
            if (name.startsWith("@/controllers/api/shared/useGet")) return load(name.slice(2) + ".ts");
            throw Error("Unexpected runtime dependency: " + name);
        };
        runInNewContext(
            code,
            {
                exports,
                require: requireModule,
                Date,
                setTimeout,
                clearTimeout,
                document: { getElementById: () => ({ scrollTo() {}, scrollTop: 0 }) },
            },
            { filename }
        );
        return exports;
    }
    return {
        calls,
        callbacks,
        load,
        respond: (fn) => {
            respond = fn;
        },
        render: (fn, ...args) => {
            cursor = 0;
            callbacks.length = 0;
            return fn(...args);
        },
    };
}

const fixtures = [
    ["controllers/api/shared/useGetPaginatedList.ts", () => [{ form: {}, limit: 2, lastCurrentDateRef: { current: new Date(0) } }], false],
    [
        "controllers/api/shared/useGetInfiniteRefreshableList.ts",
        () => [{ form: {}, limit: 2, isLastPage: false, setPage() {}, setIsLastPage() {}, lastCurrentDateRef: { current: new Date(0) } }],
        true,
    ],
    ["controllers/api/shared/botLogs/useGetBotLogs.ts", () => ["bot", { target_table: "project" }, 2], true],
    ["controllers/api/shared/botSchedules/useGetBotSchedules.ts", () => ["bot", { target_table: "project" }, 2], true],
    ["controllers/api/dashboard/useGetTrackingList.ts", () => [2], true],
    ["controllers/api/dashboard/useGetDashboardCards.ts", () => [2], true],
];

for (const [path, args, paged] of fixtures) {
    test(`${path}: failure releases single-flight and retries the same page`, async () => {
        const h = harness(),
            hook = h.load(path).default,
            input = args();
        let reject;
        h.respond(
            () =>
                new Promise((_, fail) => {
                    reject = fail;
                })
        );
        const first = h.render(hook, ...input).mutateAsync({ page: 0 });
        await h.render(hook, ...input).mutateAsync({ page: 0 });
        assert.equal(h.calls.length, 1);
        const failed = assert.rejects(first, /offline/);
        reject(Error("offline"));
        await failed;
        h.respond(async () => ({
            data: {
                records: [{ uid: "a" }, { uid: "b" }],
                logs: [{}, {}],
                schedules: [{}, {}],
                cards: [{ uid: "a" }, { uid: "b" }],
                checkitems: [{ uid: "a" }, { uid: "b" }],
                projects: [],
            },
        }));
        const request = { page: 0 };
        await h.render(hook, ...input).mutateAsync(request);
        assert.equal(h.calls.length, 2);
        if (paged)
            assert.deepEqual(
                h.calls.map((call) => call.page),
                [1, 1]
            );
        assert.equal(request.page, 0, "request input must not advance on retry");
    });
}

for (const path of ["controllers/api/shared/useGetPaginatedList.ts", "controllers/api/shared/useGetInfiniteRefreshableList.ts"]) {
    test(`${path}: zero count clears the refresh prompt without another list request`, async () => {
        const h = harness(),
            hook = h.load(path).default;
        const args = { form: {}, limit: 2, isLastPage: false, setPage() {}, setIsLastPage() {}, lastCurrentDateRef: { current: new Date(0) } };
        h.respond(async () => ({ data: { count_new_records: 5 } }));
        await h.render(hook, args).checkOutdated(new Date(0));
        assert.equal(h.render(hook, args).countNewRecords, 5);
        h.respond(async () => ({ data: { count_new_records: 0 } }));
        await h.render(hook, args).checkOutdated(new Date(0));
        assert.equal(h.render(hook, args).countNewRecords, 0);
        await h.render(hook, args).refresh();
        assert.equal(h.calls.length, 2);
    });

    test(`${path}: list responses clear zero counts and retain omitted counts`, async () => {
        const h = harness(),
            hook = h.load(path).default;
        const args = { form: {}, limit: 2, isLastPage: false, setPage() {}, setIsLastPage() {}, lastCurrentDateRef: { current: new Date(0) } };
        h.respond(async () => ({ data: { count_new_records: 5 } }));
        await h.render(hook, args).checkOutdated(new Date(0));
        h.respond(async () => ({ data: { records: [] } }));
        await h.render(hook, args).mutateAsync({ page: 0 });
        assert.equal(h.render(hook, args).countNewRecords, 5);
        h.respond(async () => ({ data: { records: [], count_new_records: 0 } }));
        await h.render(hook, args).mutateAsync({ page: 0 });
        assert.equal(h.render(hook, args).countNewRecords, 0);
    });

    test(`${path}: count and list requests follow the current scope after rerender`, async () => {
        const h = harness(),
            hook = h.load(path).default;
        const args = {
            form: { testUrl: "/first", assignee_uid: "first" },
            limit: 2,
            isLastPage: false,
            setPage() {},
            setIsLastPage() {},
            lastCurrentDateRef: { current: new Date(0) },
        };
        h.render(hook, args);
        const next = { ...args, form: { testUrl: "/second", assignee_uid: "second" } };
        h.respond(async () => ({ data: { records: [], count_new_records: 0 } }));
        await h.render(hook, next).checkOutdated(new Date(0));
        await h.render(hook, next).mutateAsync({ page: 0 });
        assert.deepEqual(
            h.calls.map(({ url, assignee_uid }) => ({ url, assignee_uid })),
            [
                { url: "/second", assignee_uid: "second" },
                { url: "/second", assignee_uid: "second" },
            ]
        );
    });

    test(`${path}: failed refresh preserves count, limit and date`, async () => {
        const h = harness(),
            hook = h.load(path).default,
            previousDate = new Date(0);
        const args = { form: {}, limit: 2, isLastPage: false, setPage() {}, setIsLastPage() {}, lastCurrentDateRef: { current: previousDate } };
        h.respond(async () => ({ data: { count_new_records: 5 } }));
        await h.render(hook, args).checkOutdated(previousDate);
        h.respond(async () => {
            throw Error("offline");
        });
        await assert.rejects(h.render(hook, args).refresh(), /offline/);
        assert.equal(args.lastCurrentDateRef.current, previousDate);
        h.respond(async () => ({ data: { records: [] } }));
        await h.render(hook, args).mutateAsync({ page: 0 });
        assert.equal(h.calls.at(-1).limit, 2);
        assert.equal(h.render(hook, args).countNewRecords, 5);
    });
}

test("infinite observer retains a failed page and releases its load gate", async () => {
    const h = harness(),
        hook = h.load("components/InfiniteScroller/useInfiniteScrollerLoaderObserver.tsx").default;
    const pages = [];
    h.render(hook, {
        hasMore: true,
        scrollable: () => null,
        children: [],
        loadMore: async (page) => {
            pages.push(page);
            if (pages.length === 1) throw Error("offline");
            return true;
        },
    });
    const load = h.callbacks[0];
    await load();
    await new Promise((done) => setTimeout(done, 5));
    await load();
    await new Promise((done) => setTimeout(done, 5));
    await load();
    assert.deepEqual(pages, [2, 2, 3]);
});

test("infinite list provider releases its own gate without advancing a failed page", async () => {
    const h = harness(),
        provider = h.load("core/providers/InfiniteRefreshableListProvider.tsx").InfiniteRefreshableListProvider;
    const props = { models: [{ created_at: new Date(1) }, { created_at: new Date(0) }], form: {}, limit: 2 };
    h.respond(async () => {
        throw Error("offline");
    });
    const context = h.render(provider, props);
    await assert.rejects(context.nextPage(), /offline/);
    assert.equal(context.isFetchingRef.current, false);
    h.respond(async () => ({ data: { records: [{}, {}] } }));
    await h.render(provider, props).nextPage();
    assert.deepEqual(
        h.calls.map((call) => call.page),
        [1, 1]
    );
});

for (const [path, exported] of [
    ["core/providers/InfiniteRefreshableListProvider.tsx", "InfiniteRefreshableListProvider"],
    ["core/providers/PaginatedListProvider.tsx", "PaginatedListProvider"],
]) {
    test(`${exported}: failed refresh releases the outer loading state`, async () => {
        const h = harness(),
            provider = h.load(path)[exported];
        const props = { models: [{ created_at: new Date(1) }], form: {}, limit: 2 };
        h.respond(async () => ({ data: { count_new_records: 5 } }));
        await h.render(provider, props).checkOutdated();
        h.respond(async () => {
            throw Error("offline");
        });
        h.render(provider, props).refreshList();
        await new Promise((done) => setImmediate(done));
        const context = h.render(provider, props);
        assert.equal(context.isFetchingRef.current, false);
        assert.equal(context.isRefreshing, false);
        assert.equal(context.countNewRecords, 5);
    });
}
