import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

// Run the production callback without loading the browser dependency tree.
const source = readFileSync(new URL("./BoardChatProvider.tsx", import.meta.url), "utf8");
const callback = source.slice(source.indexOf("    const endCallback ="), source.indexOf("    const errorCallback ="));
const code = stripTypeScriptTypes(callback, { mode: "transform" }) + "\nreturn endCallback;";

function harness() {
    const state = { task: "active", sending: true, recovering: "active" };
    const messages = new Map();
    const session = {};
    const errors = [];
    const ref = { current: state.task };
    const scope = {
        useCallback: (fn) => fn,
        ChatMessageModel: { Model: { getModel: (uid) => messages.get(uid), deleteModel: (uid) => messages.delete(uid) } },
        ChatSessionModel: { Model: { getModel: () => session } },
        chatTaskIdRef: ref,
        Toast: { Add: { error: (error) => errors.push(error) } },
        t: (key) => key,
        setIsSending: (value) => { state.sending = value; },
        setChatTaskId: (value) => { state.task = ref.current = value; },
        setRecoveringTaskId: (value) => { state.recovering = value; },
        isAtBottomRef: { current: true },
        scrollToBottomRef: { current: () => {} },
    };
    const end = new Function(...Object.keys(scope), code)(...Object.values(scope));
    const add = (uid) => {
        const message = { uid, isPending: true, chat_session_uid: "session", updated_at: new Date() };
        messages.set(uid, message);
        return message;
    };
    return { state, messages, session, errors, end, add };
}

test("another task's failure updates its message without clearing the active send or recovery", () => {
    const h = harness();
    h.add("other");
    h.end({ uid: "other", task_id: "other-task", status: "failed" });
    assert.equal(h.messages.has("other"), false);
    assert.deepEqual(h.errors, []);
    assert.deepEqual(h.state, { task: "active", sending: true, recovering: "active" });
});

test("taskless replay reconciles its message without settling an active task", () => {
    const h = harness();
    const message = h.add("replay");
    h.end({ uid: "replay", status: "success" });
    assert.equal(message.isPending, undefined);
    assert.equal(h.session.last_messaged_at, message.updated_at);
    assert.deepEqual(h.state, { task: "active", sending: true, recovering: "active" });
});

for (const status of ["success", "failed", "aborted"]) {
    test(`active ${status} settles both sending and reconnect recovery`, () => {
        const h = harness();
        h.add("own");
        h.end({ uid: "own", task_id: "active", status });
        assert.deepEqual(h.state, { task: null, sending: false, recovering: null });
        assert.equal(h.errors.length, status === "failed" ? 1 : 0);
    });
}
