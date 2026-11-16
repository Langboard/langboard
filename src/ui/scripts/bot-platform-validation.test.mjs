import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { pathToFileURL } from "node:url";
import { inflateSync } from "node:zlib";
import { createInstance } from "i18next";
import ts from "typescript";

const packet = process.env.LANGBOARD_PLATFORM_BASE64
    ? JSON.parse(inflateSync(Buffer.from(process.env.LANGBOARD_PLATFORM_BASE64, "base64")).toString())
    : null;
const ui = process.env.LANGBOARD_UI_ROOT ? pathToFileURL(`${process.env.LANGBOARD_UI_ROOT}/`) : new URL("../", import.meta.url);
const source = packet?.source ?? readFileSync(new URL("src/pages/SettingsPage/components/bots/BotCreateFormDialog.tsx", ui), "utf8");
const constants = packet?.constants ?? readFileSync(new URL("../shared/ts/src/ai/constants.ts", ui), "utf8");
function compile(source, variables = {}) {
    const exports = {};
    runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports, ...variables });
    return exports;
}
const domain = compile(constants);
const tree = ts.createSourceFile("BotCreateFormDialog.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let effect, missingError;
function visit(node) {
    if (
        ts.isCallExpression(node) &&
        node.expression.getText(tree) === "useEffect" &&
        node.arguments[0]?.getText(tree).includes("AVAILABLE_RUNNING_TYPES_BY_PLATFORM")
    )
        effect = node.arguments[0].getText(tree);
    if (
        ts.isBinaryExpression(node) &&
        node.left.getText(tree).startsWith("newErrors[key") &&
        node.right.getText(tree).includes("settings.errors.missing.bot_")
    )
        missingError = node.right.getText(tree);
    ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(missingError, "Exercise the production required-field message expression");
function normalize(platform, runningType) {
    const changes = [];
    if (effect)
        compile(`const effect = ${effect}; effect();`, {
            ...domain,
            selectedPlatform: platform,
            selectedPlatformRunningType: runningType,
            setSelectedPlatformRunningType: (value) => changes.push(value),
        });
    return changes;
}

test("creation draft aligns incompatible execution type with the actual platform registry", () => {
    const { EBotPlatform: P, EBotPlatformRunningType: R } = domain;
    assert.deepEqual(normalize(P.Langflow, R.Default), [R.Endpoint]);
    assert.deepEqual(normalize(P.Default, R.Endpoint), [R.Default]);
    assert.deepEqual(normalize(P.N8N, R.Endpoint), [R.Default]);
});

test("valid execution types preserve the current creation draft", () => {
    for (const [platform, types] of Object.entries(domain.AVAILABLE_RUNNING_TYPES_BY_PLATFORM))
        for (const runningType of types) assert.deepEqual(normalize(platform, runningType), []);
});

test("all required LangFlow fields resolve translated messages in four locales", async () => {
    for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
        const settings = JSON.parse(readFileSync(new URL(`src/assets/locales/${locale}/settings.json`, ui), "utf8"));
        const i18n = createInstance();
        await i18n.init({ lng: locale, fallbackLng: false, resources: { [locale]: { settings } }, ns: ["settings"], nsSeparator: "." });
        for (const key of ["name", "uname", "url", "apiKey", "value"]) {
            const { message } = compile(`exports.message = ${missingError};`, { key, t: i18n.t.bind(i18n) });
            assert.ok(message && !message.startsWith("settings."), `${locale}/${key}: ${message}`);
            if (key === "value") assert.equal(message, settings.errors.missing.internal_bot_value);
        }
    }
});
