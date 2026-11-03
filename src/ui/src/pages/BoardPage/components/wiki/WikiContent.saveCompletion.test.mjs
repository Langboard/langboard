import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./WikiContent.tsx", import.meta.url), "utf8");
const callback = source.slice(source.indexOf("    const handleSave ="), source.indexOf("    const handleCancel ="));

function harness() {
    let resolve;
    let reject;
    const mutation = new Promise((yes, no) => { resolve = yes; reject = no; });
    const state = { editing: true, reset: false };
    const scope = {
        useCallback: (fn) => fn,
        editEpoch: { current: 0 },
        editorRef: { current: null },
        sanitizeEditorContent: (value) => value,
        content: { content: "edited" },
        capturedSource: { current: { generation: 3, content: "original" } },
        wikiMarkdownRevision: async () => "revision",
        changeWikiDetailsMutateAsync: () => mutation,
        project: { uid: "board" },
        wiki: { uid: "wiki" },
        resetSection: () => { state.reset = true; },
        setIsEditing: (value) => { state.editing = value; },
        t: (key) => key,
        setupApiErrorHandler: () => ({ handle: () => {} }),
        EHttpStatus: { HTTP_403_FORBIDDEN: 403 },
        navigate: () => {},
        ROUTES: { BOARD: { WIKI: () => "wiki" } },
        // Sonner returns a handle synchronously; completion belongs to unwrap().
        Toast: { Add: { promise: (promise, options) => {
            const completion = promise.then(options.success, (error) => { options.error(error); throw error; });
            completion.catch(() => {});
            return { unwrap: () => completion };
        } } },
    };
    scope.editorRef.current = { tf: { deselect: () => {} }, api: { markdown: { serialize: () => "next" } } };
    const save = new Function(...Object.keys(scope), `${callback}\nreturn handleSave;`)(...Object.values(scope));
    return { save, resolve, reject, state };
}

test("wiki section save remains pending until persistence and the success callback finish", async () => {
    const h = harness();
    let settled = false;
    const completion = h.save().then(() => { settled = true; });
    await new Promise(setImmediate);
    assert.equal(settled, false);
    assert.equal(h.state.editing, true);
    h.resolve({});
    await completion;
    assert.equal(settled, true);
    assert.deepEqual(h.state, { editing: false, reset: true });
});

test("failed persistence rejects section save and retains editing state", async () => {
    const h = harness();
    const completion = h.save();
    const failure = assert.rejects(completion, /save failed/);
    h.reject(new Error("save failed"));
    await failure;
    assert.deepEqual(h.state, { editing: true, reset: false });
});
