import assert from "node:assert/strict";
import { test } from "node:test";
import { createPlateEditor } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";
import { BaseH1Plugin } from "@platejs/basic-nodes";
import { YjsPlugin } from "@platejs/yjs/react";
import * as Y from "yjs";
import { Awareness } from "y-protocols/awareness";
import type { XmlText } from "yjs";
import { yTextToSlateElement } from "@slate-yjs/core";
import { MarkdownSourcePlugin, applySourceValue, matchingInitialSource, preservedSource } from "./markdown-source-plugin.ts";

test("source deltas preserve node identity, untouched blocks and live selection references", () => {
    const editor = createPlateEditor({
        plugins: [MarkdownSourcePlugin],
        value: [
            { type: "p", id: "first", children: [{ text: "Hello" }] },
            { type: "p", id: "last", children: [{ text: "Untouched" }] },
        ],
    });
    const point = editor.api.pointRef({ path: [1, 0], offset: 4 });
    applySourceValue(editor, [
        { type: "p", children: [{ text: "Hello world", bold: true }] },
        { type: "p", children: [{ text: "Untouched" }] },
    ]);
    assert.equal(editor.children[0].id, "first");
    assert.equal(editor.children[1].id, "last");
    assert.deepEqual(editor.children[0].children, [{ text: "Hello world", bold: true }]);
    assert.deepEqual(point.current, { path: [1, 0], offset: 4 });
    point.unref();
});

test("exact source is retained until a document edit, while fragment exports bypass it", () => {
    assert.equal(preservedSource(createPlateEditor()), undefined);
    const editor = createPlateEditor({ plugins: [MarkdownSourcePlugin], value: [{ type: "p", children: [{ text: "Hello" }] }] });
    editor.getApi(MarkdownSourcePlugin).markdownSource.remember("Hello  \n");
    assert.equal(preservedSource(editor), "Hello  \n");
    assert.equal(preservedSource(editor, { value: editor.children }), undefined);
    assert.equal(preservedSource(editor, { allowedNodes: ["p"] }), undefined);
    editor.tf.insertText("!", { at: { path: [0, 0], offset: 5 } });
    assert.equal(preservedSource(editor), undefined);
});

test("source structural changes normalize an empty document and retain valid sibling nodes", () => {
    const editor = createPlateEditor({ plugins: [MarkdownSourcePlugin], value: [{ type: "p", children: [{ text: "Before" }] }] });
    applySourceValue(editor, [
        { type: "h2", children: [{ text: "Heading" }] },
        { type: "p", children: [{ text: "After" }] },
    ]);
    assert.equal(editor.children[0].type, "h2");
    assert.equal(editor.children[1].children[0].text, "After");
    applySourceValue(editor, [{ type: "p", children: [{ text: "" }] }]);
    assert.equal(editor.children.length, 1);
    assert.equal(editor.api.string([]), "");
});

test("initial source survives a generated trailing block and delayed collaboration; different live content wins", () => {
    const editor = createPlateEditor({
        plugins: [MarkdownSourcePlugin, MarkdownPlugin, BaseH1Plugin],
        value: [{ type: "p", children: [{ text: "" }] }],
    });
    const source = "# Heading";
    assert.equal(matchingInitialSource(editor, source), undefined);
    applySourceValue(editor, [
        { type: "h1", children: [{ text: "Heading" }] },
        { type: "p", children: [{ text: "" }] },
    ]);
    assert.equal(matchingInitialSource(editor, source), source);
    assert.equal(matchingInitialSource(editor, "# Other"), undefined);
});

test("source edits update the live Yjs document without dropping untouched content", async () => {
    const providerDocument = new Y.Doc();
    const awareness = new Awareness(providerDocument);
    const editor = createPlateEditor({
        plugins: [
            MarkdownSourcePlugin,
            YjsPlugin.configure({
                options: {
                    providers: [
                        {
                            type: "test",
                            document: providerDocument,
                            awareness,
                            connect() {},
                            disconnect() {},
                            destroy() {},
                            isConnected: true,
                            isSynced: true,
                        },
                    ],
                },
            }),
        ],
    });
    const api = editor.getApi(YjsPlugin).yjs;
    const doc = editor.getOptions(YjsPlugin).ydoc;
    try {
        await api.init({
            autoConnect: false,
            value: [
                { type: "p", id: "edited", children: [{ text: "Before" }] },
                { type: "p", id: "retained", children: [{ text: "Remote content" }] },
            ],
        });
        applySourceValue(editor, [
            { type: "p", children: [{ text: "After" }] },
            { type: "p", children: [{ text: "Remote content" }] },
        ]);
        await new Promise<void>((resolve) => queueMicrotask(resolve));
        const content = yTextToSlateElement(doc.get("content") as XmlText);
        assert.equal(content.children[0].id, "edited");
        assert.equal(content.children[1].id, "retained");
        assert.ok(JSON.stringify(content).includes("After"));
        assert.ok(JSON.stringify(content).includes("Remote content"));
        assert.ok(!JSON.stringify(content).includes("Before"));
    } finally {
        api.destroy();
        editor.getOptions(YjsPlugin).awareness?.destroy();
        doc.destroy();
        awareness.destroy();
        providerDocument.destroy();
    }
});
