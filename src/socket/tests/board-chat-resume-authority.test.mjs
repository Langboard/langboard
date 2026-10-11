import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

// Execute the production event handler, substituting storage/transport boundaries.
const source = fs.readFileSync("src/events/BoardChat.ts", "utf8");
const ast = ts.createSourceFile("BoardChat.ts", source, ts.ScriptTarget.Latest, true);
let handler;
let approvalValue;
for (const node of ast.statements) {
    if (ts.isVariableStatement(node) && node.declarationList.declarations.some(d => d.name.getText(ast) === "getApprovalRequestValue")) approvalValue = node.getText(ast);
    if (ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)) {
        const args = node.expression.arguments;
        if (args[1]?.getText(ast) === "SocketEvents.CLIENT.BOARD.CHAT.RESUME") handler = args[2].getText(ast);
    }
}
assert(handler && approvalValue);
const emitted = ts.transpileModule(`${approvalValue}\nreturn (${handler});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const names = ["Utils", "ESocketStatus", "SnowflakeID", "ChatHistory", "ProjectChatSession", "ChatSession", "ESocketTopic", "SocketEvents", "createGraphResumePayload", "api", "DEFAULT_GRAPH_URL", "AI_REQUEST_TIMEOUT", "createGraphServiceHeaders", "resolveGraphApprovalFromChatResume", "sanitizeGraphResumeMessage", "EGraphApprovalStatus", "createResolvedGraphInterrupt"];
for (const scenario of ["owner", "foreign_owner", "wrong_board", "swapped_thread", "missing_interrupt", "missing_message", "missing_session"]) {
    const calls = [];
    const errors = [];
    const message = { id: "10", chat_session_id: "20", message: { content: "Existing", graph_interrupt: scenario === "missing_interrupt" ? null : { value: { type: "approval_request", thread_id: "stored-thread" } } }, save: async () => calls.push("save") };
    const deps = {
        Utils: { Type: { isString: x => typeof x === "string", isObject: x => !!x && typeof x === "object", isError: x => x instanceof Error } },
        ESocketStatus: { WS_4001_INVALID_DATA: "invalid" },
        SnowflakeID: { fromShortCode: x => ({ toString: () => x }) },
        ChatHistory: { findOne: async () => scenario === "missing_message" ? null : message },
        ProjectChatSession: { findOne: async ({ where }) => { assert.equal(where.project_id, "board"); return scenario === "wrong_board" ? null : { uid: "board-session" }; } },
        ChatSession: { findOne: async ({ where }) => { assert.deepEqual(where, { id: "20", user_id: "owner" }); return ["foreign_owner", "missing_session"].includes(scenario) ? null : { user_id: "owner" }; }, update: async () => calls.push("update") },
        ESocketTopic: { Board: "board" }, SocketEvents: { SERVER: { BOARD: { CHAT: { STREAM: "stream" } } } },
        createGraphResumePayload: () => ({ approved: false, rejected: true }),
        api: { post: async (url, payload) => { calls.push("post"); assert.equal(url, "http://graph/api/v1/graph/resume/stored-thread"); assert.equal(payload.session_id, "stored-thread"); return { data: { message: "Existing", interrupts: [] } }; } },
        DEFAULT_GRAPH_URL: "http://graph", AI_REQUEST_TIMEOUT: 5,
        createGraphServiceHeaders: () => ({ Authorization: "fixture" }),
        resolveGraphApprovalFromChatResume: async () => null,
        sanitizeGraphResumeMessage: x => x,
        EGraphApprovalStatus: { Rejected: "rejected" }, createResolvedGraphInterrupt: () => null,
    };
    const execute = new Function(...names, emitted)(...names.map(n => deps[n]));
    await execute({ client: { user: { id: "owner" }, sendError: (...args) => errors.push(args), stream: () => ({ buffer() {}, end() {} }) }, topicId: "board", data: { message_uid: "10", thread_id: scenario === "swapped_thread" ? "foreign-thread" : "stored-thread", session_id: "forged-session", resume: false } });
    if (scenario === "owner") { assert(calls.includes("post")); assert.equal(errors.length, 0); }
    else { assert(!calls.includes("post")); assert(!calls.includes("save")); assert(!calls.includes("update")); assert.equal(errors.length, 1); }
}
console.log("7 production resume-handler authority cases passed; transport/storage mocked");
