import assert from "node:assert/strict";
import { test } from "node:test";
import { Buffer } from "node:buffer";
import { createPlateEditor } from "platejs/react";
import { TrailingBlockPlugin } from "platejs";
import { MarkdownPlugin } from "@platejs/markdown";
import { YjsPlugin } from "@platejs/yjs/react";
import * as Y from "yjs";
import { YjsEditor } from "@slate-yjs/core";
import { splitDecorationsByChild } from "slate-dom";
import { deferCollaborativeConnection, getCollaborativeDocumentValue, initializeCollaborativeDraft } from "./collaborativeInitialization.ts";
import { prepareRichDraftPatch } from "./prepareRichDraftPatch.ts";
import { sanitizeEditorContent } from "./utils.ts";

const documentID = "card:native-restoration-regression:description";
const original = "# Original body\n\nRESTORE-BASE-MARKER\n\nLast section";
const retained = `${original}\n\nNewest retained local draft`;
const flush = () => new Promise((resolve) => globalThis.queueMicrotask(resolve));
const base64 = (update) => Buffer.from(update).toString("base64");

function peer(value, stored) {
    const editor = createPlateEditor({
        plugins: [
            MarkdownPlugin,
            TrailingBlockPlugin,
            YjsPlugin.configure({
                options: { providers: [{ type: "local-test", connect() {}, disconnect() {}, destroy() {}, isConnected: false, isSynced: false }] },
            }),
        ],
        value: (instance) => instance.getApi(MarkdownPlugin).markdown.deserialize(value),
        autoSelect: false,
    });
    const { ydoc: doc, awareness } = editor.getOptions(YjsPlugin);
    assert.ok(doc instanceof Y.Doc);
    if (stored) Y.applyUpdate(doc, stored);
    const markdown = editor.getApi(MarkdownPlugin).markdown;
    const nativeConnect = editor.connect;
    const renderSnapshots = [];
    const assertRenderable = () => {
        const visit = (node) => {
            assert.ok(Array.isArray(node.children), "Slate Element requires an actual children array");
            assert.doesNotThrow(() => splitDecorationsByChild(editor, node, []));
            node.children.forEach((child) => {
                if (typeof child.text === "string") return;
                visit(child);
            });
        };
        editor.children.forEach(visit);
        renderSnapshots.push(globalThis.structuredClone(editor.children));
    };
    const originalOnChange = editor.onChange;
    editor.onChange = (...args) => {
        assertRenderable();
        return originalOnChange(...args);
    };
    let binding;
    return {
        editor,
        doc,
        markdown,
        nativeConnect,
        renderSnapshots,
        assertRenderable,
        text: () => sanitizeEditorContent(markdown.serialize()),
        init: () => {
            binding = deferCollaborativeConnection(editor);
            return binding.initialize(() =>
                editor
                    .getApi(YjsPlugin)
                    .yjs.init({ id: documentID, autoConnect: false, autoSelect: false, selection: null, value: null, onReady: assertRenderable })
            );
        },
        resume: () => binding.resume(),
        disposeBinding: () => binding.dispose(),
        restore: (restore) =>
            initializeCollaborativeDraft({
                documentID,
                doc,
                value: sanitizeEditorContent(markdown.serialize({ value: markdown.deserialize(value) })),
                currentValue: sanitizeEditorContent(markdown.serialize({ value: getCollaborativeDocumentValue(doc) })),
                restore,
            }),
        destroy() {
            binding?.dispose();
            editor.getApi(YjsPlugin).yjs.destroy();
            awareness.destroy();
            doc.destroy();
        },
    };
}

function prepared(snapshot, value, deserialize) {
    const response = prepareRichDraftPatch(JSON.stringify({ type: "rich_patch_prepare", request_id: "a".repeat(32), snapshot, value }), deserialize);
    assert.ok(response);
    return Buffer.from(JSON.parse(response).update, "base64");
}

test("automatic restore defers native seeding even when two retained values differ", async () => {
    const a = peer(retained);
    const b = peer("A different retained local draft");
    try {
        await Promise.all([a.init(), b.init()]);
        await flush();
        assert.deepEqual(Y.encodeStateAsUpdate(a.doc), Uint8Array.of(0, 0));
        assert.deepEqual(Y.encodeStateAsUpdate(b.doc), Uint8Array.of(0, 0));
        assert.equal(a.text(), retained);
        assert.equal(b.text(), "A different retained local draft");
        assert.equal(a.editor.api.isNormalizing(), true);
        assert.equal(YjsEditor.connected(a.editor), false);
        assert.ok(a.renderSnapshots.length);
        const requests = [];
        for (const current of [a, b])
            assert.equal(
                await current.restore(async (request) => {
                    requests.push(request);
                    return false;
                }),
                false
            );
        assert.equal(requests.length, 2);
        assert.equal(requests[0].restore_snapshot, requests[1].restore_snapshot);
        assert.notEqual(requests[0].value, requests[1].value);
        assert.deepEqual(Y.encodeStateAsUpdate(a.doc), Uint8Array.of(0, 0));
        assert.deepEqual(Y.encodeStateAsUpdate(b.doc), Uint8Array.of(0, 0));
    } finally {
        a.destroy();
        b.destroy();
    }
});

test("one owner-prepared replacement converges two stale peers and native reopen to one retained body", async () => {
    const stale = peer("Stale persisted Yjs body");
    let a;
    let b;
    let reopened;
    try {
        await stale.init();
        const seed = prepared(base64(Y.encodeStateAsUpdate(stale.doc)), "Stale persisted Yjs body", stale.markdown.deserialize);
        Y.applyUpdate(stale.doc, seed);
        const snapshot = Y.encodeStateAsUpdate(stale.doc);
        a = peer(retained, snapshot);
        b = peer(retained, snapshot);
        await Promise.all([a.init(), b.init()]);
        assert.deepEqual(Y.encodeStateAsUpdate(a.doc), snapshot);
        assert.deepEqual(Y.encodeStateAsUpdate(b.doc), snapshot);
        const requests = [];
        for (const current of [a, b])
            await current.restore(async (request) => {
                requests.push(request);
                return false;
            });
        assert.equal(a.text(), retained);
        assert.equal(b.text(), retained);
        assert.equal(a.markdown.serialize({ value: getCollaborativeDocumentValue(a.doc) }).trim(), "Stale persisted Yjs body");
        assert.equal(requests[0].restore_snapshot, requests[1].restore_snapshot);

        // The real Phoenix owner admission/CAS is covered by DocumentTest. Here
        // exercise its existing prepared-update producer and native consumers.
        const update = prepared(requests[0].restore_snapshot, requests[0].value, a.markdown.deserialize);
        for (const current of [a, b]) Y.applyUpdate(current.doc, update);
        a.resume();
        b.resume();
        await flush();
        Y.applyUpdate(a.doc, Y.encodeStateAsUpdate(b.doc));
        Y.applyUpdate(b.doc, Y.encodeStateAsUpdate(a.doc));
        await flush();
        assert.equal(a.text(), retained);
        assert.equal(b.text(), retained);
        assert.equal(a.text().split("RESTORE-BASE-MARKER").length - 1, 1);
        reopened = peer(original, Y.encodeStateAsUpdate(a.doc));
        await reopened.init();
        reopened.resume();
        assert.equal(reopened.text(), retained);
    } finally {
        reopened?.destroy();
        a?.destroy();
        b?.destroy();
        stale.destroy();
    }
});

test("an unchanged synchronized value is a no-op and avoids redundant replacement", async () => {
    const current = peer(original);
    try {
        await current.init();
        Y.applyUpdate(current.doc, prepared(base64(Y.encodeStateAsUpdate(current.doc)), original, current.markdown.deserialize));
        await flush();
        const before = Y.encodeStateAsUpdate(current.doc);
        assert.equal(
            await current.restore(async () => {
                assert.fail("Equal content must not request replacement");
            }),
            true
        );
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), before);
        current.resume();
        current.assertRenderable();
    } finally {
        current.destroy();
    }
});

test("restore snapshots include deletes even when a state vector cannot detect the change", async () => {
    const doc = new Y.Doc();
    const clone = new Y.Doc();
    try {
        const root = doc.get("content", Y.XmlText);
        root.insert(0, "Deleted unsaved content");
        const vector = Y.encodeStateVector(doc);
        root.delete(0, root.length);
        assert.deepEqual(Y.encodeStateVector(doc), vector);
        let requests = 0;
        assert.equal(
            await initializeCollaborativeDraft({
                documentID,
                doc,
                value: retained,
                currentValue: "",
                restore: async (request) => {
                    requests++;
                    Y.applyUpdate(clone, Buffer.from(request.restore_snapshot, "base64"));
                    assert.deepEqual(Y.encodeStateAsUpdate(clone), Y.encodeStateAsUpdate(doc));
                    return false;
                },
            }),
            false
        );
        assert.equal(requests, 1);
        assert.equal(clone.get("content", Y.XmlText).toString(), "");
    } finally {
        doc.destroy();
        clone.destroy();
    }
});

test("conflicts and transport failures never mutate or rebase a synchronized local document", async () => {
    const current = peer(retained);
    try {
        await current.init();
        let requests = 0;
        const originalSnapshot = Y.encodeStateAsUpdate(current.doc);
        assert.equal(
            await current.restore(async () => {
                requests++;
                return false;
            }),
            false
        );
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), originalSnapshot);
        assert.equal(requests, 1);
        await assert.rejects(
            current.restore(async () => {
                requests++;
                throw new Error("Owner unavailable");
            }),
            /Owner unavailable/
        );
        assert.equal(requests, 2);
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), originalSnapshot);
    } finally {
        current.destroy();
    }
});

test("a busy peer waits without normalization writes or retry until the winning owner update arrives", async () => {
    const current = peer(retained);
    try {
        await current.init();
        let requests = 0;
        let completed = false;
        const pending = initializeCollaborativeDraft({
            documentID,
            doc: current.doc,
            value: retained,
            currentValue: current.text(),
            restore: async () => {
                requests++;
                return "busy";
            },
        }).then((result) => {
            completed = true;
            return result;
        });
        await flush();
        assert.equal(completed, false);
        assert.equal(requests, 1);
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), Uint8Array.of(0, 0));
        const update = prepared(base64(Y.encodeStateAsUpdate(current.doc)), retained, current.markdown.deserialize);
        Y.applyUpdate(current.doc, update);
        assert.equal(await pending, true);
        current.resume();
        await flush();
        assert.equal(current.text(), retained);
        assert.equal(current.text().split("RESTORE-BASE-MARKER").length - 1, 1);
        assert.equal(requests, 1);
    } finally {
        current.destroy();
    }
});

test("empty synchronized state keeps the retained Slate tree valid at every pending render boundary", async () => {
    const current = peer(retained);
    try {
        await current.init();
        await flush();
        current.assertRenderable();
        assert.equal(current.text(), retained);
        assert.equal(current.editor.api.isNormalizing(), true);
        assert.equal(YjsEditor.connected(current.editor), false);
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), Uint8Array.of(0, 0));
        const update = prepared(base64(Y.encodeStateAsUpdate(current.doc)), "Actual owner body", current.markdown.deserialize);
        Y.applyUpdate(current.doc, update);
        assert.equal(current.text(), retained, "The pending editor keeps its valid retained value until the native connection resumes");
        current.resume();
        await flush();
        current.assertRenderable();
        assert.equal(YjsEditor.connected(current.editor), true);
        assert.equal(current.text(), "Actual owner body");
        for (const value of current.renderSnapshots) {
            assert.ok(value.every((node) => Array.isArray(node.children)));
        }
    } finally {
        current.destroy();
    }
});

test("disposing a pending native connection restores its lifecycle without connecting or publishing a seed", async () => {
    const current = peer(retained);
    try {
        await current.init();
        current.disposeBinding();
        assert.equal(current.editor.connect, current.nativeConnect);
        current.resume();
        assert.equal(YjsEditor.connected(current.editor), false);
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), Uint8Array.of(0, 0));
        current.assertRenderable();
        assert.equal(current.text(), retained);
    } finally {
        current.destroy();
    }
});

test("dispose before native initial sync never connects or publishes a blank shared paragraph", async () => {
    const editor = createPlateEditor({
        plugins: [
            MarkdownPlugin,
            TrailingBlockPlugin,
            YjsPlugin.configure({
                options: {
                    providers: [
                        { type: "unsynchronized-local-test", connect() {}, disconnect() {}, destroy() {}, isConnected: true, isSynced: false },
                    ],
                },
            }),
        ],
        value: [{ type: "p", children: [{ text: "Retained draft awaiting native initial sync" }] }],
    });
    const options = editor.getOptions(YjsPlugin);
    const nativeConnect = editor.connect;
    const binding = deferCollaborativeConnection(editor);
    let retry;
    try {
        const initialization = binding.initialize(() =>
            editor.getApi(YjsPlugin).yjs.init({ id: documentID, value: null, autoSelect: false, selection: null })
        );
        binding.dispose();
        // Exercise the real native5s initial-sync timeout, including its late
        // connect call. This ordering differs from disposal after await init.
        await initialization;
        await flush();
        assert.deepEqual(
            {
                connected: YjsEditor.connected(editor),
                sharedBytes: Y.encodeStateAsUpdate(options.ydoc).length,
                rootLength: options.ydoc.get("content", Y.XmlText).length,
            },
            { connected: false, sharedBytes: 2, rootLength: 0 }
        );
        assert.deepEqual(Y.encodeStateAsUpdate(options.ydoc), Uint8Array.of(0, 0));
        assert.equal(options.ydoc.get("content", Y.XmlText).length, 0);
        assert.equal(editor.connect, nativeConnect);

        // The same native editor can be reused only after predecessor teardown.
        editor.getApi(YjsPlugin).yjs.destroy();
        retry = deferCollaborativeConnection(editor);
        await retry.initialize(() => editor.getApi(YjsPlugin).yjs.init({ id: documentID, autoConnect: false, value: null }));
        binding.dispose();
        assert.notEqual(editor.connect, nativeConnect, "Old cleanup cannot replace the retry's connection gate");
        assert.equal(YjsEditor.connected(editor), false);
        assert.deepEqual(Y.encodeStateAsUpdate(options.ydoc), Uint8Array.of(0, 0));
        const markdown = editor.getApi(MarkdownPlugin).markdown;
        Y.applyUpdate(options.ydoc, prepared(base64(Y.encodeStateAsUpdate(options.ydoc)), retained, markdown.deserialize));
        retry.resume();
        await flush();
        assert.equal(YjsEditor.connected(editor), true);
        assert.equal(editor.connect, nativeConnect);
        assert.equal(sanitizeEditorContent(markdown.serialize()), retained);
    } finally {
        editor.getApi(YjsPlugin).yjs.destroy();
        retry?.dispose();
        binding.dispose();
        options.awareness.destroy();
        options.ydoc.destroy();
    }
});

test("unmount aborts a busy wait and preserves the retained seed without a replacement", async () => {
    const current = peer(retained);
    try {
        await current.init();
        const abort = new globalThis.AbortController();
        const pending = initializeCollaborativeDraft({
            documentID,
            doc: current.doc,
            value: retained,
            currentValue: current.text(),
            restore: async () => "busy",
            signal: abort.signal,
        });
        abort.abort();
        await assert.rejects(pending, /initialization ended/);
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), Uint8Array.of(0, 0));
    } finally {
        current.destroy();
    }
});

test("the native lifecycle cancellation signal reaches the authenticated restore transport", async () => {
    const current = peer(retained);
    try {
        await current.init();
        const abort = new globalThis.AbortController();
        let signal;
        await initializeCollaborativeDraft({
            documentID,
            doc: current.doc,
            value: retained,
            currentValue: current.text(),
            signal: abort.signal,
            restore: async (_request, passedSignal) => {
                signal = passedSignal;
                return false;
            },
        });
        assert.equal(signal, abort.signal);
        assert.deepEqual(Y.encodeStateAsUpdate(current.doc), Uint8Array.of(0, 0));
    } finally {
        current.destroy();
    }
});
