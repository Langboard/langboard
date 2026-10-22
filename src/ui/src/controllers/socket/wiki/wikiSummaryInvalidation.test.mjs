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
            ProjectWiki: { Model: { fromOne: (value) => mutations.push(value), deleteModel: (uid) => mutations.push(uid) } },
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
