import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { URL } from "node:url";
import { setImmediate } from "node:timers";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const { QueryClient, QueryObserver } = require("@tanstack/react-query");
const compiled = ts.transpileModule(readFileSync(new URL("./useGetProjects.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const settle = () => new Promise((resolve) => setImmediate(resolve));

function harness() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const requests = [],
        writes = [],
        deletes = [];
    let actor = "actor-a",
        version = 1,
        identityVersion = 1,
        observer,
        unsubscribe;
    const auth = (selector) => selector(auth.getState());
    auth.getState = () => ({
        currentUser: actor ? { uid: actor } : null,
        getSessionVersion: () => version,
        getIdentityVersion: () => identityVersion,
    });
    const dependencies = {
        react: { useEffect() {}, useId: () => "test-view" },
        "@/core/stores/AuthStore": { default: auth },
        "@/core/providers/SocketProvider": {},
        "./projectWorkloadSubscriptions": { acquireProjectWorkload() {} },
        axios: { isAxiosError: () => false },
        "@langboard/core/constants": { Routing: { API: { DASHBOARD: { PROJECTS: "/dashboard/projects" } } }, SocketEvents: {} },
        "@langboard/core/enums": { EHttpStatus: { HTTP_404_NOT_FOUND: 404 }, ESocketTopic: { Dashboard: "dashboard" } },
        "@/core/helpers/Api": { api: { get: (_url, config) => new Promise((resolve) => requests.push({ config, resolve })) } },
        "@/core/helpers/ModelHelper": { deleteProjectModel: (...args) => deletes.push(args) },
        "@/core/models": Object.fromEntries(
            ["Project", "ProjectColumn"].map((name) => [
                name,
                {
                    Model: {
                        fromArray: (values) => {
                            writes.push({ name, values });
                            return values;
                        },
                        getModels: () => [],
                        deleteModel: (uid) => deletes.push(uid),
                    },
                },
            ])
        ),
        "@/core/helpers/QueryMutation": {
            useQueryMutation: () => ({
                queryClient: client,
                query: (queryKey, queryFn, options) => {
                    if (observer) observer.setOptions({ ...options, queryKey, queryFn });
                    else {
                        observer = new QueryObserver(client, { ...options, queryKey, queryFn });
                        unsubscribe = observer.subscribe(() => {});
                    }
                    return observer.getCurrentResult();
                },
            }),
        },
    };
    const exports = {};
    vm.runInNewContext(compiled, { exports, require: (name) => dependencies[name] });
    return {
        client,
        requests,
        writes,
        deletes,
        render: (options) => exports.default(options),
        identity: (uid) => {
            if (uid !== actor) identityVersion++;
            actor = uid;
            version++;
        },
        observer: () => observer,
        close: () => {
            unsubscribe?.();
            client.clear();
        },
    };
}
const response = (uid) => ({ data: { projects: [{ uid }], columns: [] } });

for (const replacement of ["actor-b", null]) {
    test(`late dashboard response cannot hydrate models after actor replacement with ${replacement}`, async () => {
        const state = harness();
        try {
            state.render();
            assert.equal(state.requests.length, 1);
            state.identity(replacement);
            // No rerender/cancellation yet: the actor fence itself protects model writes.
            assert.equal(state.requests[0].config.signal.aborted, false);
            state.requests[0].resolve(response("old-private-project"));
            await settle();
            assert.equal(state.writes.length, 0);
            assert.equal(state.deletes.length, 0);
            assert.equal(state.observer().getCurrentResult().isError, true);
        } finally {
            state.close();
        }
    });
}

test("actor caches isolate accounts and preserve caller enabled/select behavior", async () => {
    const state = harness();
    const select = (data) => ({ ...data, selected: true });
    try {
        state.render({ select, enabled: false, staleTime: Infinity, refetchOnWindowFocus: true });
        assert.equal(state.requests.length, 0);
        state.render({ select, staleTime: Infinity, refetchOnWindowFocus: true });
        state.requests[0].resolve(response("account-a-project"));
        await settle();
        assert.equal(state.observer().getCurrentResult().data.selected, true);
        state.identity("actor-a");
        const refreshed = state.render({ select, staleTime: Infinity });
        assert.equal(state.requests.length, 1, "same-user token refresh reuses cache");
        assert.equal(refreshed.data.projects[0].uid, "account-a-project");
        state.identity("actor-b");
        const next = state.render({ select, staleTime: Infinity });
        assert.equal(next.data, undefined, "new account cannot observe previous actor cache");
        assert.equal(state.requests.length, 2);
        state.requests[1].resolve(response("account-b-project"));
        await settle();
        assert.equal(state.observer().getCurrentResult().data.projects[0].uid, "account-b-project");
    } finally {
        state.close();
    }
});

test("same-user token refresh during a pending read completes without another request or error", async () => {
    const state = harness();
    try {
        state.render();
        state.identity("actor-a");
        state.render();
        assert.equal(state.requests.length, 1);
        assert.equal(state.requests[0].config.signal.aborted, false);
        state.requests[0].resolve(response("current-project"));
        await settle();
        assert.equal(state.observer().getCurrentResult().isSuccess, true);
        assert.equal(state.writes.length, 2);
        assert.equal(state.requests.length, 1);
    } finally {
        state.close();
    }
});

test("logout/relogin as same UID clears and aborts old query even when transport ignores cancellation", async () => {
    const state = harness();
    try {
        state.render();
        state.client.clear(); // AuthStore.removeToken clears the shared client synchronously.
        state.identity(null);
        state.identity("actor-a");
        assert.equal(state.requests[0].config.signal.aborted, true);
        state.requests[0].resolve(response("cancelled-private-project"));
        await settle();
        assert.equal(state.writes.length, 0);
        assert.equal(state.deletes.length, 0);
        assert.equal(state.client.getQueryData(["get-dashboard-projects", "actor-a"]), undefined);
    } finally {
        state.close();
    }
});

test("anonymous dashboard response cannot hydrate global models", async () => {
    const state = harness();
    try {
        state.identity(null);
        state.render();
        state.requests[0].resolve(response("unauthenticated-project"));
        await settle();
        assert.equal(state.writes.length, 0);
        assert.equal(state.deletes.length, 0);
        assert.equal(state.observer().getCurrentResult().isError, true);
    } finally {
        state.close();
    }
});

test("identity fence rejects a prior login response even after relogin with the same UID", async () => {
    const state = harness();
    try {
        state.render();
        state.identity(null);
        state.identity("actor-a");
        // Exercise the identity generation fence independently of normal cache cancellation.
        assert.equal(state.requests[0].config.signal.aborted, false);
        state.requests[0].resolve(response("prior-login-private-project"));
        await settle();
        assert.equal(state.writes.length, 0);
        assert.equal(state.deletes.length, 0);
        assert.equal(state.observer().getCurrentResult().isError, true);
    } finally {
        state.close();
    }
});
