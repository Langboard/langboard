import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import assert from "node:assert/strict";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const { QueryClient, isCancelledError } = require("@tanstack/react-query");
const source = readFileSync(new URL("./useGetMetadata.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function setup(transport) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const requests = [];
    const writes = [];
    const keys = [];
    const dependencies = {
        "@langboard/core/constants": {
            Routing: {
                API: {
                    METADATA: {
                        CARD: "/board/{uid}/card/{card_uid}/metadata",
                        PROJECT_WIKI: "/board/{uid}/wiki/{wiki_uid}/metadata",
                    },
                },
            },
        },
        "@/core/helpers/Api": {
            api: {
                get: async (url, options) => {
                    requests.push({ url, signal: options.signal });
                    return transport ? transport(url, options) : { data: { metadata: { source: url } } };
                },
            },
        },
        "@/core/helpers/QueryMutation": {
            useQueryMutation: () => ({
                query: (queryKey, queryFn, options) => {
                    keys.push(queryKey);
                    return client.fetchQuery({ ...options, queryKey, queryFn });
                },
            }),
        },
        "@/core/models": { MetadataModel: { Model: { fromOne: (model) => writes.push(model) } } },
        "@langboard/core/utils": { Utils: { String: { format: (value, params) => value.replace(/\{(\w+)\}/g, (_, key) => params[key]) } } },
    };
    const exports = {};
    vm.runInNewContext(compiled, {
        exports,
        require: (name) => {
            assert.ok(dependencies[name], `Unexpected dependency ${name}`);
            return dependencies[name];
        },
    });
    return { client, requests, writes, keys, read: exports.default };
}

for (const [label, first, second] of [
    ["different cards", { type: "card", project_uid: "board", uid: "one" }, { type: "card", project_uid: "board", uid: "two" }],
    ["card and wiki", { type: "card", project_uid: "board", uid: "one" }, { type: "project_wiki", project_uid: "board", uid: "one" }],
    ["different boards", { type: "card", project_uid: "board-one", uid: "one" }, { type: "card", project_uid: "board-two", uid: "one" }],
]) {
    test(`concurrent metadata for ${label} uses independent queries and model writes`, async () => {
        const h = setup();
        try {
            await Promise.all([h.read(first), h.read(second)]);
            assert.equal(h.requests.length, 2);
            assert.equal(h.writes.length, 2);
            assert.notDeepEqual([...h.keys[0]], [...h.keys[1]]);
            assert.deepEqual(
                h.writes.map((model) => model.metadata.source),
                h.requests.map((request) => request.url)
            );
        } finally {
            h.client.clear();
        }
    });
}

test("concurrent reads of the same metadata still share one transport", async () => {
    const h = setup();
    try {
        const form = { type: "card", project_uid: "board", uid: "one" };
        await Promise.all([h.read(form), h.read({ ...form })]);
        assert.equal(h.requests.length, 1);
        assert.equal(h.writes.length, 1);
    } finally {
        h.client.clear();
    }
});

test("cancelling one metadata query aborts its transport without writing stale models or cancelling another card", async () => {
    const h = setup((url, { signal }) =>
        url.includes("/one/")
            ? new Promise((_resolve, reject) => {
                  signal?.addEventListener("abort", () => reject(new Error("Transport aborted")), { once: true });
              })
            : { data: { metadata: { source: url } } }
    );
    try {
        const cancelled = h.read({ type: "card", project_uid: "board", uid: "one" }).catch((error) => error);
        const other = h.read({ type: "card", project_uid: "board", uid: "two" });
        await h.client.cancelQueries({ queryKey: h.keys[0], exact: true });
        assert.ok(isCancelledError(await cancelled));
        await other;
        assert.equal(h.requests[0].signal?.aborted, true);
        assert.equal(h.writes.length, 1);
        assert.equal(h.writes[0].uid, "two");
    } finally {
        h.client.clear();
    }
});
