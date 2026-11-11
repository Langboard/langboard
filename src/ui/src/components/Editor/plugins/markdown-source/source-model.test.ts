import assert from "node:assert/strict";
import { test } from "node:test";
import { mapSource, pointSource, sourcePoint, sourceKey, mergeSource } from "./source-model.ts";

test("source offsets survive heading markers, emphasis, links and Korean text", () => {
    const source = "## 안녕 **세계** [링크](https://example.com)";
    const leaf = (value: string) => {
        const start = source.indexOf(value);
        return { type: "text", value, position: { start: { offset: start }, end: { offset: start + value.length } } };
    };
    const map = mapSource(source, { type: "root", children: [leaf("안녕 "), leaf("세계"), leaf("링크")] }, [
        { type: "h2", children: [{ text: "안녕 " }, { text: "세계", bold: true }, { type: "a", children: [{ text: "링크" }] }] },
    ]);
    assert.deepEqual(sourcePoint(map, source.indexOf("계")), { path: [0, 1], offset: 1 });
    assert.equal(pointSource(map, { path: [0, 2, 0], offset: 1 }), source.indexOf("크"));
    assert.equal(sourcePoint([], 0), undefined);
});

test("list, task and quote continuation mirrors TOAST without checking the next task", () => {
    assert.equal(sourceKey("9. item", 7, 7, "Enter")?.value, "9. item\n10. ");
    assert.equal(sourceKey("- [x] task", 10, 10, "Enter")?.value, "- [x] task\n- [ ] ");
    assert.equal(sourceKey("> quote", 7, 7, "Enter")?.value, "> quote\n> ");
    assert.equal(sourceKey("- ", 2, 2, "Enter")?.value, "");
    assert.equal(sourceKey("```\n- literal", 13, 13, "Enter"), undefined);
    assert.equal(sourceKey("- item", 1, 4, "Enter"), undefined);
});

test("format shortcuts preserve selection and toggle existing delimiters", () => {
    const edit = sourceKey("hello", 1, 4, "b", true)!;
    assert.deepEqual(edit, { value: "h**ell**o", start: 3, end: 6 });
    assert.deepEqual(sourceKey(edit.value, edit.start, edit.end, "b", true), { value: "hello", start: 1, end: 4 });
});

test("Tab and Shift+Tab indent selected lines, including selection ending at next line", () => {
    assert.deepEqual(sourceKey("a\nb\nc", 0, 4, "Tab"), { value: "    a\n    b\nc", start: 4, end: 12 });
    assert.equal(sourceKey("    a\n    b", 0, 11, "Tab", false, true)?.value, "a\nb");
});

test("independent composition and remote changes merge; overlapping replacements remain explicit", () => {
    assert.equal(mergeSource("hello world", "hello! world", "hello world!"), "hello! world!");
    assert.equal(mergeSource("abc", "aXc", "aYc"), undefined);
    assert.equal(mergeSource("abc", "aXbc", "aYbc"), undefined);
    assert.equal(mergeSource("abc", "abc", "def"), "def");
});

test("entity contents are not mistaken for subsequent rendered characters", () => {
    const source = "&amp;a";
    const map = mapSource(source, { type: "text", value: "&a", position: { start: { offset: 0 }, end: { offset: 6 } } }, [
        { type: "p", children: [{ text: "&a" }] },
    ]);
    assert.equal(pointSource(map, { path: [0, 0], offset: 1 }), 5);
});
