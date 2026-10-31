import assert from "node:assert/strict";
import { test } from "node:test";
import { createSlateEditor } from "platejs";
import { MarkdownPlugin } from "@platejs/markdown";
import { mapWikiSecretSelection, wikiMarkdownRevision } from "./wiki-secret-selection.ts";

function fixture(source: string, start: number, end: number) {
    const editor = createSlateEditor({ plugins: [MarkdownPlugin], value: [{ type: "p", children: [{ text: source }] }] });
    editor.selection = { anchor: { path: [0, 0], offset: start }, focus: { path: [0, 0], offset: end } };
    return editor;
}
test("repeated Korean and emoji map by exact UTF16 offsets, reversed range supported, live value untouched", () => {
    const source = "같은😀 같은😀 tail";
    const editor = fixture(source, 11, 6);
    const before = JSON.stringify(editor.children);
    assert.deepEqual(mapWikiSecretSelection(editor, source), { start_utf16: 6, end_utf16: 11 });
    assert.equal(JSON.stringify(editor.children), before);
});
test("serializer terminal LF accepted only against exact source", () => {
    const editor = fixture("literal", 0, 7);
    assert.deepEqual(mapWikiSecretSelection(editor, "literal"), { start_utf16: 0, end_utf16: 7 });
    assert.equal(mapWikiSecretSelection(editor, "literal "), null);
});
test("reject surrogate split, empty, rich, reference, cross-block and normalized source", () => {
    assert.equal(mapWikiSecretSelection(fixture("😀", 0, 1), "😀"), null);
    assert.equal(mapWikiSecretSelection(fixture("same", 0, 0), "same"), null);
    for (const source of ["**bold**", "[x](secret://ref/a)", "a\nb", "a\\b", "# title"]) {
        assert.equal(mapWikiSecretSelection(fixture(source, 0, source.length), source), null);
    }
    const editor = fixture("text", 0, 4);
    editor.children[0].children[0].bold = true;
    assert.equal(mapWikiSecretSelection(editor, "**text**"), null);
    editor.selection!.focus.path = [1, 0];
    assert.equal(mapWikiSecretSelection(editor, "text"), null);
});
test("revision hashes exact UTF8, including Unicode and terminal newline", async () => {
    const { createHash } = await import("node:crypto");
    for (const source of ["한글😀", "한글😀\n"])
        assert.equal(await wikiMarkdownRevision(source), createHash("sha256").update(source, "utf8").digest("hex"));
});
