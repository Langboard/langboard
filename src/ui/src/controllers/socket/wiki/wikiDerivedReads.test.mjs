import fs from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import ts from "typescript";

function converter(wiki, client) {
    const name = "useBoardWikiDetailsChangedHandlers";
    const source = fs
        .readFileSync(`src/controllers/socket/wiki/${name}.ts`, "utf8")
        .replace(/^import .*;\n/gm, "")
        .replace(/export interface /g, "interface ")
        .replace(`export default ${name};`, `return ${name};`);
    const dependencies = {
        queryClient: client,
        resetWorkspaceSummaryQueries() {},
        useSocketHandler: (options) => options.onProps.responseConverter,
        ESocketTopic: { BoardWiki: "board_wiki", BoardWikiPrivate: "board_wiki_private" },
        SocketEvents: { SERVER: { BOARD: { WIKI: {} } } },
    };
    return new Function(
        ...Object.keys(dependencies),
        ts.transpileModule(source, {
            compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
        }).outputText
    )(...Object.values(dependencies))({ projectUID: "board", wiki });
}

test("late details snapshots cannot restore a secretized editor generation", () => {
    const wiki = { uid: "wiki", editor_generation: 1, content: { content: "[masked]" }, title: "Current" };
    const callback = converter(wiki, { cancelQueries() {}, invalidateQueries() {} });
    callback({ editor_generation: 0, content: { content: "fixture-sensitive-value" }, title: "Stale" });
    assert.equal(wiki.editor_generation, 1);
    assert.equal(wiki.content.content, "[masked]");
    assert.equal(wiki.title, "Current");
});

test("new generation removes obsolete body before failed hydration; ordinary updates preserve it", () => {
    const reads = [];
    const wiki = { uid: "wiki", editor_generation: 0, content: { content: "fixture-sensitive-value" }, title: "Current" };
    const callback = converter(wiki, {
        cancelQueries() {},
        invalidateQueries: (options) => {
            reads.push(options);
            return Promise.resolve();
        },
    });
    callback({ title: "Ordinary" });
    assert.equal(wiki.content.content, "fixture-sensitive-value");
    assert.equal(wiki.editor_generation, 0);
    callback({ editor_generation: 1 });
    assert.equal(wiki.content, undefined);
    assert.equal(wiki.editor_generation, 1);
    assert.deepEqual(reads, [{ queryKey: ["get-wikis-board"], exact: true }]);
    callback({ editor_generation: 0, content: { content: "fixture-sensitive-value" } });
    assert.equal(wiki.content, undefined);
});

function nativeFactory(name, dependencies, path = `src/controllers/socket/wiki/${name}.ts`) {
    const source = fs
        .readFileSync(path, "utf8")
        .replace(/^import .*;\n/gm, "")
        .replace(/export interface /g, "interface ")
        .replace(`export default ${name};`, `return ${name};`);
    return new Function(
        ...Object.keys(dependencies),
        ts.transpileModule(source, {
            compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
        }).outputText
    )(...Object.values(dependencies));
}

test("delayed creation metadata never replays body or generation into existing wiki", () => {
    const wiki = { uid: "wiki", editor_generation: 2, content: { content: "[masked]" }, title: "Current", forbidden: true };
    const reads = [];
    const factory = nativeFactory("useBoardWikiCreatedHandlers", {
        queryClient: { cancelQueries() {}, invalidateQueries: (options) => reads.push(options) },
        resetWorkspaceSummaryQueries() {},
        useSocketHandler: (options) => options.onProps.responseConverter,
        ESocketTopic: { BoardWiki: "board_wiki" },
        SocketEvents: { SERVER: { BOARD: { WIKI: {} } } },
        ProjectWiki: { Model: { getModel: () => wiki, fromOne: (patch) => Object.assign(wiki, patch) } },
    });
    factory({ projectUID: "board" })({
        wiki: {
            uid: "wiki",
            title: "Created",
            editor_generation: 0,
            content: { content: "fixture-sensitive-value" },
            is_public: true,
            assigned_members: [],
        },
    });
    assert.equal(wiki.content.content, "[masked]");
    assert.equal(wiki.editor_generation, 2);
    assert.equal(wiki.title, "Current");
    assert.equal(wiki.forbidden, true);
    assert.deepEqual(reads, [{ queryKey: ["get-wikis-board"], exact: true }]);
});

import { QueryClient, QueryObserver } from "@tanstack/react-query";
for (const outcome of ["masked", "revoked", "failed"]) {
    test(`native list hydration cancels old reads and handles ${outcome} without restoring old body`, async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wiki = { uid: "wiki", editor_generation: 0, content: { content: "fixture-sensitive-value" }, forbidden: false };
        const otherWiki = { uid: "other", editor_generation: 3, content: { content: "Other body" } };
        const pending = [];
        let queryFn;
        const reader = nativeFactory(
            "useGetWikis",
            {
                useAuthStore: { getState: () => ({}) },
                workspaceSummaryAuthScope: () => "actor:1:1",
                Routing: { API: { BOARD: { WIKI: { GET_ALL: "/board/{uid}/wikis" } } } },
                Utils: { String: { format: () => "/board/board/wikis" } },
                api: {
                    get: (_, { signal }) =>
                        new Promise((resolve, reject) => {
                            signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
                            pending.push({ resolve, reject, signal });
                        }),
                },
                useQueryMutation: () => ({
                    query: (_, fn) => {
                        queryFn = fn;
                    },
                }),
                ProjectWiki: {
                    Model: {
                        getModel: (uid) => (uid === wiki.uid ? wiki : otherWiki),
                        fromArray: (items) => items.map((item) => Object.assign(item.uid === wiki.uid ? wiki : otherWiki, item)),
                    },
                },
                User: { Model: { fromArray: (items) => items } },
            },
            "src/controllers/api/wiki/useGetWikis.ts"
        );
        reader({ project_uid: "board" });
        const observer = new QueryObserver(client, { queryKey: ["get-wikis-board"], queryFn, retry: false });
        const release = observer.subscribe(() => {});
        assert.equal(pending.length, 1);
        const callback = converter(wiki, client);
        callback({ editor_generation: 1 });
        assert.equal(wiki.content, undefined);
        assert.equal(pending[0].signal.aborted, true);
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(pending.length, 2);
        pending[0].resolve({
            data: { wikis: [{ uid: "wiki", editor_generation: 0, content: { content: "fixture-sensitive-value" } }], project_members: [] },
        });
        if (outcome === "failed") pending[1].reject(new Error("read unavailable"));
        else
            pending[1].resolve({
                data: {
                    wikis: [
                        outcome === "masked"
                            ? { uid: "wiki", editor_generation: 1, content: { content: "[masked]" }, forbidden: false }
                            : { uid: "wiki", editor_generation: 1, content: undefined, forbidden: true },
                    ],
                    project_members: [],
                },
            });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(wiki.editor_generation, 1);
        assert.equal(wiki.content?.content, outcome === "masked" ? "[masked]" : undefined);
        assert.equal(wiki.forbidden, outcome === "revoked");
        assert.equal(otherWiki.content.content, "Other body");
        callback({ editor_generation: 0, content: { content: "fixture-sensitive-value" } });
        assert.equal(wiki.content?.content, outcome === "masked" ? "[masked]" : undefined);
        release();
        client.clear();
    });
}

for (const outcome of ["replica", "missing-generation", "account", "abort", "revoked-replica"]) {
    test(`native hydration rejects ${outcome} stale editor response`, async () => {
        const wiki = { uid: "wiki", editor_generation: 1, content: undefined, forbidden: false, title: "Current" };
        let auth = "actor:1:1";
        const controller = new AbortController();
        let resolve;
        let read;
        const reader = nativeFactory(
            "useGetWikis",
            {
                useAuthStore: { getState: () => ({}) },
                workspaceSummaryAuthScope: () => auth,
                Routing: { API: { BOARD: { WIKI: { GET_ALL: "/wikis" } } } },
                Utils: { String: { format: () => "/wikis" } },
                api: {
                    get: () =>
                        new Promise((done) => {
                            resolve = done;
                        }),
                },
                useQueryMutation: () => ({
                    query: (_, fn) => {
                        read = fn;
                    },
                }),
                ProjectWiki: { Model: { getModel: () => wiki, fromArray: (items) => items.map((item) => Object.assign(wiki, item)) } },
                User: { Model: { fromArray: (items) => items } },
            },
            "src/controllers/api/wiki/useGetWikis.ts"
        );
        reader({ project_uid: "board" });
        const promise = read({ signal: controller.signal });
        if (outcome === "account") auth = "other:2:2";
        if (outcome === "abort") controller.abort();
        resolve({
            data: {
                wikis: [
                    {
                        uid: "wiki",
                        title: "Metadata",
                        forbidden: outcome === "revoked-replica",
                        ...(outcome === "missing-generation" ? {} : { editor_generation: 0 }),
                        content: { content: "fixture-sensitive-value" },
                    },
                ],
                project_members: [],
            },
        });
        if (outcome === "account" || outcome === "abort") await assert.rejects(promise, /superseded/);
        else {
            await promise;
            assert.equal(wiki.title, "Metadata");
        }
        assert.equal(wiki.editor_generation, 1);
        assert.equal(wiki.content, undefined);
        assert.equal(wiki.forbidden, outcome === "revoked-replica");
    });
}

test("secretization fences a newly created model before initial body hydration", () => {
    const wiki = { uid: "wiki", content: undefined };
    const callback = converter(wiki, { cancelQueries() {}, invalidateQueries() {} });
    callback({ editor_generation: 1 });
    assert.equal(wiki.editor_generation, 1);
    assert.equal(wiki.content, undefined);
});
