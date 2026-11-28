import fs from "node:fs";
import assert from "node:assert/strict";
import ts from "typescript";
import { QueryClient } from "@tanstack/react-query";
const invalidation = {};
new Function(
    "exports",
    ts.transpileModule(fs.readFileSync("src/controllers/api/WorkspaceSummaryInvalidation.ts", "utf8"), {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText
)(invalidation);
const queryClient = new QueryClient();
let actor = "actor-a";
const auth = { getState: () => ({ currentUser: { uid: actor }, getIdentityVersion: () => 1, getSessionVersion: () => 1 }) };
const source = fs
    .readFileSync("src/controllers/socket/card/useCardRelationshipsUpdatedHandlers.ts", "utf8")
    .replace(/^import .*;\n/gm, "")
    .replace(/export interface /g, "interface ")
    .replace("export default useCardRelationshipsUpdatedHandlers;", "return useCardRelationshipsUpdatedHandlers;");
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const reads = [];
const synchronized = [];
const hook = new Function(
    "useSocketHandler",
    "ESocketTopic",
    "SocketEvents",
    "Routing",
    "Utils",
    "api",
    "syncCardRelationships",
    "queryClient",
    "resetWorkspaceSummaryQueries",
    "workspaceSummaryAuthScope",
    "useAuthStore",
    code
)(
    (options) => options.onProps.responseConverter,
    { Board: "board" },
    { SERVER: { BOARD: { CARD: { RELATIONSHIPS_UPDATED: "updated" } } } },
    { API: { BOARD: { CARD: { UPDATE_RELATIONSHIPS: "/relationships" } } } },
    { String: { format: (value) => value } },
    { get: () => new Promise((resolve, reject) => reads.push({ resolve, reject })) },
    (value) => synchronized.push(value),
    queryClient,
    invalidation.resetWorkspaceSummaryQueries,
    invalidation.workspaceSummaryAuthScope,
    auth
);
const convert = hook({ projectUID: "board" });
const summaryKey = ["card-relation-summaries", "actor-a:1:1", "board", "card"];
queryClient.setQueryData(summaryKey, { pages: [{ title: "Revoked private summary" }], pageParams: [null] });
const first = convert({ card_uid: "card", relationships_invalidated: true });
assert.equal(queryClient.getQueryData(summaryKey), undefined, "native converter synchronously retires private summary metadata");
const second = convert({ card_uid: "card", relationships_invalidated: true });
reads[1].resolve({ data: { relationships: ["current"] } });
await second;
reads[0].resolve({ data: { relationships: ["obsolete"] } });
await first;
assert.deepEqual(
    synchronized.map((x) => x.relationships),
    [[], [], ["current"]]
);
const denied = convert({ card_uid: "card", relationships_invalidated: true });
reads[2].reject(new Error("access revoked"));
await denied;
assert.deepEqual(synchronized.at(-1).relationships, []);
const stale = convert({ card_uid: "card", relationships_invalidated: true });
await convert({ card_uid: "card", relationships: ["legacy-current"] });
reads[3].resolve({ data: { relationships: ["obsolete"] } });
await stale;
assert.deepEqual(synchronized.at(-1).relationships, ["legacy-current"]);
const oldActor = convert({ card_uid: "card", relationships_invalidated: true });
actor = "actor-b";
reads[4].resolve({ data: { relationships: ["old-actor"] } });
await oldActor;
assert.deepEqual(synchronized.at(-1).relationships, [], "old-actor async authorization cannot restore native edges");
const readCount = reads.length;
await hook({ projectUID: "board", summaryOnly: true })({ card_uid: "panel-only", relationships_invalidated: true });
assert.equal(reads.length, readCount, "panel-only events never fetch full relationship models");
queryClient.clear();
