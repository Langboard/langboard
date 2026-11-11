import assert from "node:assert/strict";
import { test } from "node:test";
import { createPlateEditor } from "platejs/react";
import { MarkdownPlugin, markdownToAstProcessor } from "@platejs/markdown";
import { BaseH1Plugin } from "@platejs/basic-nodes";
import { YjsPlugin } from "@platejs/yjs/react";
import { MarkdownSourcePlugin, applySourceValue, matchingInitialSource, prepareSourceCommit, preservedSource } from "./markdown-source-plugin.ts";
import { mapSource, pointSource, type DocumentNode } from "./source-model.ts";

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

test("hard and soft Markdown line breaks retain mappings after Slate merges text leaves", () => {
    for (const source of ["first  \nsecond", "first\\\nsecond", "first\nsecond"]) {
        const editor = createPlateEditor({ plugins: [MarkdownSourcePlugin, MarkdownPlugin] });
        applySourceValue(editor, editor.getApi(MarkdownPlugin).markdown.deserialize(source));
        const map = mapSource(source, markdownToAstProcessor(editor, source), editor.children as DocumentNode[]);
        assert.equal(editor.api.string([0]), "first\nsecond");
        assert.equal(pointSource(map, { path: [0, 0], offset: 6 }), source.indexOf("second"));
        assert.equal(pointSource(map, { path: [0, 0], offset: 5 }), source.indexOf("\n"));
    }
});

test("input received before the remote-change effect preserves a disjoint peer edit", () => {
    const editor = createPlateEditor({
        plugins: [MarkdownSourcePlugin, MarkdownPlugin],
        value: [
            { type: "p", children: [{ text: "First" }] },
            { type: "p", children: [{ text: "Second" }] },
        ],
    });
    const markdown = editor.getApi(MarkdownPlugin).markdown;
    const base = markdown.serialize(),
        snapshot = JSON.stringify(editor.children);
    editor.tf.insertText(" remote", { at: { path: [1, 0], offset: 6 } });
    const prepared = prepareSourceCommit(editor, snapshot, base, base.replace("First", "First local"));
    assert.equal(prepared.status, "ready");
    assert.ok(prepared.source.includes("First local"));
    assert.ok(prepared.source.includes("Second remote"));
    if (prepared.status === "ready") applySourceValue(editor, markdown.deserialize(prepared.source));
    assert.equal(editor.api.string([0]), "First local");
    assert.equal(editor.api.string([1]), "Second remote");
});

test("overlapping input before the remote-change effect never mutates the peer document", () => {
    const editor = createPlateEditor({ plugins: [MarkdownSourcePlugin, MarkdownPlugin], value: [{ type: "p", children: [{ text: "Base" }] }] });
    const markdown = editor.getApi(MarkdownPlugin).markdown;
    const base = markdown.serialize(),
        snapshot = JSON.stringify(editor.children);
    applySourceValue(editor, [{ type: "p", children: [{ text: "Peer" }] }]);
    const prepared = prepareSourceCommit(editor, snapshot, base, "Mine\n");
    assert.equal(prepared.status, "conflict");
    assert.equal(editor.api.string([0]), "Peer");
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
    const editor = createPlateEditor({ plugins: [MarkdownSourcePlugin, YjsPlugin.configure({ options: { providers: [] } })] });
    const api = editor.getApi(YjsPlugin).yjs;
    const { ydoc: doc, awareness } = editor.getOptions(YjsPlugin);
    editor.setOptions(YjsPlugin, {
        providers: [
            {
                type: "test",
                document: doc,
                awareness,
                connect() {},
                disconnect() {},
                destroy() {},
                isConnected: true,
                isSynced: true,
            },
        ],
    });
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
        const content = doc.get("content").toString();
        assert.equal(editor.children[0].id, "edited");
        assert.equal(editor.children[1].id, "retained");
        assert.ok(content.includes("After"));
        assert.ok(content.includes("Remote content"));
        assert.ok(!content.includes("Before"));
    } finally {
        api.destroy();
        editor.getOptions(YjsPlugin).awareness?.destroy();
        doc.destroy();
    }
});
