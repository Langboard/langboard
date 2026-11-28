import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { inflateSync } from "node:zlib";
import { KEYS } from "platejs";
import { BlockPlaceholderPlugin, createPlateEditor } from "platejs/react";

const packet = process.env.LANGBOARD_BLOCK_PLACEHOLDER_BASE64
    ? JSON.parse(inflateSync(Buffer.from(process.env.LANGBOARD_BLOCK_PLACEHOLDER_BASE64, "base64")).toString())
    : null;
const ui = process.env.LANGBOARD_UI_ROOT ? pathToFileURL(`${process.env.LANGBOARD_UI_ROOT}/`) : new URL("../", import.meta.url);
const hook = packet?.hook ?? readFileSync(new URL("src/components/Editor/useCreateEditor.tsx", ui), "utf8");
const kit = packet?.kit ?? readFileSync(new URL("src/components/Editor/plugins/block-placeholder-kit.tsx", ui), "utf8");
const locales = ["en-US", "ko-KR", "ja-JP", "zh-CN"];
const resources = Object.fromEntries(
    locales.map((locale) => [
        locale,
        packet?.resources[locale] ?? JSON.parse(readFileSync(new URL(`src/assets/locales/${locale}/editor.json`, ui), "utf8")),
    ])
);

function editor() {
    const body = stripTypeScriptTypes(kit.replace(/^import[^\n]+\n/gm, "").replace("export const BlockPlaceholderKit", "const BlockPlaceholderKit"));
    const plugins = new Function("KEYS", "BlockPlaceholderPlugin", `${body}\nreturn BlockPlaceholderKit;`)(KEYS, BlockPlaceholderPlugin);
    return createPlateEditor({
        plugins,
        value: [
            { type: KEYS.p, children: [{ text: "Draft remains" }] },
            { type: KEYS.p, children: [{ text: "" }] },
        ],
    });
}

function mountLocaleEffect(currentEditor) {
    const option = hook.indexOf('editor.setOption(BlockPlaceholderPlugin, "placeholders"');
    assert.ok(option >= 0, "production locale effect is missing");
    const start = hook.lastIndexOf("useEffect(", option);
    const end = hook.indexOf("]);", option) + 3;
    assert.ok(start >= 0 && end > option);
    const apply = new Function("useEffect", "editor", "BlockPlaceholderPlugin", "KEYS", "t", hook.slice(start, end));
    let previous;
    const useEffect = (run, dependencies) => {
        if (!previous || dependencies.some((dependency, index) => !Object.is(dependency, previous[index]))) run();
        previous = dependencies;
    };
    return (t) => apply(useEffect, currentEditor, BlockPlaceholderPlugin, KEYS, t);
}

test("native block placeholder starts blank until the active locale is applied", () => {
    assert.equal(editor().getOption(BlockPlaceholderPlugin, "placeholders")[KEYS.p], "");
});

test("the production locale effect updates the real Plate option in four locales", () => {
    const current = editor();
    const render = mountLocaleEffect(current);
    for (const locale of locales) {
        const expected = resources[locale]["Type something..."];
        assert.equal(typeof expected, "string", `${locale}: translation missing`);
        render((key) => (key === "editor.Type something..." ? expected : key));
        assert.equal(current.getOption(BlockPlaceholderPlugin, "placeholders")[KEYS.p], expected, locale);
    }
});

test("locale option updates preserve the editor draft, selection and operations", () => {
    const current = editor();
    current.selection = { anchor: { path: [0, 0], offset: 5 }, focus: { path: [0, 0], offset: 5 } };
    current.setOption(BlockPlaceholderPlugin, "placeholders", { [KEYS.p]: "", h1: "Custom heading" });
    const children = current.children,
        selection = current.selection,
        operations = current.operations.slice();
    const render = mountLocaleEffect(current);
    for (const locale of locales) render(() => resources[locale]["Type something..."]);
    assert.equal(current.getOption(BlockPlaceholderPlugin, "placeholders").h1, "Custom heading");
    assert.equal(current.children, children);
    assert.equal(current.selection, selection);
    assert.deepEqual(current.operations, operations);
});
