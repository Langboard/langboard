import fs from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import ts from "typescript";
import { QueryClient } from "@tanstack/react-query";

const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS };
const invalidation = {};
new Function(
    "exports",
    ts.transpileModule(fs.readFileSync("src/controllers/api/WorkspaceSummaryInvalidation.ts", "utf8"), { compilerOptions }).outputText
)(invalidation);

for (const [handler, data] of [
    ["AssigneesUpdated", { assigned_members: [] }],
    ["PublicChanged", { wiki: { uid: "wiki", is_public: false } }],
    ["DetailsChanged", { title: "New title" }],
    ["Created", { wiki: { uid: "created" } }],
    ["Deleted", { uid: "wiki" }],
]) {
    test(`native Wiki ${handler} converter resets summary cache and retains legacy conversion`, () => {
        const client = new QueryClient();
        const key = ["wiki-summaries", "actor:1:1", "board"];
        client.setQueryData(key, { pages: [{ items: [{ title: "Revoked private title" }] }], pageParams: [null] });
        const mutations = [];
        const wiki = { uid: "wiki", is_public: false, assigned_members: [], changeToPrivate: () => mutations.push("private") };
        const dependencies = {
            queryClient: client,
            resetWorkspaceSummaryQueries: invalidation.resetWorkspaceSummaryQueries,
            useSocketHandler: (options) => options.onProps.responseConverter,
            useSocketOutsideProvider: () => ({ isSubscribed: () => true }),
            ESocketTopic: { BoardWiki: "board_wiki", BoardWikiPrivate: "board_wiki_private" },
            SocketEvents: { SERVER: { BOARD: { WIKI: {} } } },
            ProjectWiki: { Model: { getModel: () => undefined, fromOne: (value) => mutations.push(value), deleteModel: (uid) => mutations.push(uid) } },
        };
        const name = `useBoardWiki${handler}Handlers`;
        const source = fs
            .readFileSync(`src/controllers/socket/wiki/${name}.ts`, "utf8")
            .replace(/^import .*;\n/gm, "")
            .replace(/export interface /g, "interface ")
            .replace(`export default ${name};`, `return ${name};`);
        const factory = new Function(...Object.keys(dependencies), ts.transpileModule(source, { compilerOptions }).outputText)(
            ...Object.values(dependencies)
        );
        factory({ projectUID: "board", wiki, currentUser: { uid: "actor", is_admin: false } })(data);
        assert.equal(client.getQueryData(key), undefined);
        if (handler === "DetailsChanged") assert.equal(wiki.title, "New title");
        else assert.ok(mutations.length, "the existing native model update remains active");
        client.clear();
    });
}


const name = "useBoardWikiPublicChangedHandlers";
const source = fs.readFileSync(`src/controllers/socket/wiki/${name}.ts`, "utf8")
    .replace(/^import .*;\n/gm, "")
    .replace(/export interface /g, "interface ")
    .replace(`export default ${name};`, `return ${name};`);
const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
}).outputText;
test("publicity metadata and legacy events preserve editor state, ACL, and authorized hydration", async () => {
for (const legacy of [false, true]) {
    for (const mode of ["public", "assigned", "admin", "revoked", "newly-public"]) {
        const wiki = {
            uid: "wiki", title: "Current title", content: {content: "[masked]"},
            editor_generation: 1, is_public: true, assigned_members: [], forbidden: mode === "newly-public",
            changeToPrivate() { this.content = undefined; this.title = ""; this.forbidden = true; }
        };
        const patches = [];
        const reads = [];
        const dependencies = {
            queryClient: {invalidateQueries(options) { reads.push(options); }}, resetWorkspaceSummaryQueries() {},
            useSocketHandler: options => options.onProps.responseConverter,
            ESocketTopic: {BoardWiki: "board_wiki"},
            SocketEvents: {SERVER: {BOARD: {WIKI: {}}}},
            ProjectWiki: {Model: {fromOne(patch) { patches.push(patch); Object.assign(wiki, patch); }}}
        };
        const factory = new Function(...Object.keys(dependencies), compiled)(...Object.values(dependencies));
        const payload = {
            uid: "wiki", is_public: mode === "public" || mode === "newly-public",
            assigned_members: mode === "assigned" ? [{uid: "actor"}] : [],
            ...(legacy ? {content: {content: "fixture-sensitive-value"}, editor_generation: 0, title: "Old title"} : {})
        };
        factory({projectUID: "board", wiki, currentUser: {uid: "actor", is_admin: mode === "admin"}})({wiki: payload});
        assert.equal(wiki.editor_generation, 1);
        assert.deepEqual(Object.keys(patches[0]).sort(), ["assigned_members", "created_at", "is_public", "uid", "updated_at"]);
        if (mode === "revoked") {
            assert.equal(wiki.forbidden, true);
            assert.equal(wiki.content, undefined);
        } else if (mode === "newly-public") {
            assert.equal(wiki.forbidden, true, "read must succeed before revealing the placeholder");
            assert.deepEqual(reads, [{queryKey: ["get-wikis-board"], exact: true}]);
            // Execute the existing authorized list reader used by the invalidated observer.
            const readerSource = fs.readFileSync("src/controllers/api/wiki/useGetWikis.ts", "utf8")
                .replace(/^import .*;\n/gm, "")
                .replace(/export interface /g, "interface ")
                .replace("export default useGetWikis;", "return useGetWikis;");
            const readerDependencies = {
                useAuthStore: {getState: () => ({})}, workspaceSummaryAuthScope: () => "actor:1:1",
                Routing: {API: {BOARD: {WIKI: {GET_ALL: "/board/{uid}/wikis"}}}},
                Utils: {String: {format: () => "/board/board/wikis"}},
                api: {get: async () => ({data: {wikis: [{uid: "wiki", content: {content: "[masked]"},
                    editor_generation: 1, forbidden: false, title: "Authorized title"}], project_members: []}})},
                useQueryMutation: () => ({query: (key, fn) => {
                    assert.deepEqual(key, reads[0].queryKey);
                    return fn({signal: new AbortController().signal});
                }}),
                ProjectWiki: {Model: {getModel: () => wiki, fromArray: items => items.map(item => Object.assign(wiki, item))}},
                User: {Model: {fromArray: items => items}}
            };
            const reader = new Function(...Object.keys(readerDependencies), ts.transpileModule(readerSource, {
                compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}
            }).outputText)(...Object.values(readerDependencies));
            await reader({project_uid: "board"});
            assert.equal(wiki.forbidden, false);
            assert.equal(wiki.content.content, "[masked]");
            assert.equal(wiki.editor_generation, 1);
        } else {
            assert.equal(wiki.forbidden, false);
            assert.equal(wiki.content.content, "[masked]");
            assert.equal(wiki.title, "Current title");
        }
    }
}
console.log("10 native publicity converter cases and authorized hydration passed");
});
