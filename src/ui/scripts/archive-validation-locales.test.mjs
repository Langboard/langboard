import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { inflateSync } from "node:zlib";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createInstance } from "i18next";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { I18nextProvider } = require("react-i18next");
const packet = process.env.LANGBOARD_ARCHIVE_VALIDATION_BASE64
    ? JSON.parse(inflateSync(Buffer.from(process.env.LANGBOARD_ARCHIVE_VALIDATION_BASE64, "base64")).toString())
    : null;
const ui = process.env.LANGBOARD_UI_ROOT ? pathToFileURL(`${process.env.LANGBOARD_UI_ROOT}/`) : new URL("../", import.meta.url);
const source = packet?.source ?? readFileSync(new URL("src/pages/BoardPage/components/settings/BoardSettingsBasic.tsx", ui), "utf8");
const load = (source, imports = {}, globals = {}) => {
    const exports = {};
    runInNewContext(
        ts.transpileModule(source, {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
        }).outputText,
        { exports, require: (name) => imports[name] ?? require(name), ...globals }
    );
    return exports;
};
const Form = load(readFileSync(new URL("src/components/base/Form/index.tsx", ui), "utf8")).default;
const container = ({ children }) => React.createElement("div", null, children);
const FormErrorMessage = load(readFileSync(new URL("src/components/FormErrorMessage/index.tsx", ui), "utf8"), {
    "@/components/base/Form": { default: Form },
    "@/components/base/Box": { default: container },
    "@/components/base/Flex": { default: container },
    "@/components/base/IconComponent": { default: () => null },
    "@/core/utils/ComponentUtils": { cn: () => "" },
}).default;
const { convertValidationToLangKey } = load(readFileSync(new URL("src/core/hooks/form/utils.ts", ui), "utf8"));
const field = source.indexOf('<Form.Field name="archive_visible_days">');
const start = source.lastIndexOf("<Label", field);
const end = source.indexOf("{aiDescription", field);
assert.ok(field >= 0 && start >= 0 && end > field);
const custom = source.match(/archive_visible_days:\s*\{[\s\S]*?errorKey:\s*"([^"]+)"/)[1];
const keys = ["required", `custom:${custom}`].map((rule) => convertValidationToLangKey("project.errors", rule, "archive_visible_days"));
const locales = ["en-US", "ko-KR", "ja-JP", "zh-CN"];
const resources = Object.fromEntries(
    locales.map((locale) => [
        locale,
        {
            translation: {
                project: packet?.resources[locale] ?? JSON.parse(readFileSync(new URL(`src/assets/locales/${locale}/project.json`, ui), "utf8")),
            },
        },
    ])
);
const i18n = createInstance();
await i18n.init({ resources, lng: "en-US", fallbackLng: false });
const fieldErrors = {};
const ArchiveField = load(
    `export default function ArchiveField() { return <Form.Root>${source.slice(start, end)}</Form.Root>; }`,
    {},
    {
        Form,
        FormErrorMessage,
        Label: container,
        Box: container,
        Collaborative: { Input: ({ defaultValue }) => React.createElement("input", { name: "archive_visible_days", defaultValue }) },
        EEditorCollaborationType: { BoardSettings: "board-settings" },
        project: { uid: "qa" },
        archiveVisibleDays: 3,
        isBasicInfoEditing: true,
        isValidating: false,
        t: i18n.t.bind(i18n),
        errors: fieldErrors,
    }
).default;
const render = () => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, React.createElement(ArchiveField)));

test("both generated archive validation keys resolve in four locales", async () => {
    for (const locale of locales) {
        await i18n.changeLanguage(locale);
        for (const key of keys) assert.equal(i18n.exists(key), true, `${locale}: ${key}`);
    }
});
test("actual archive error fragment renders within its production Form.Field", async () => {
    for (const locale of locales) {
        await i18n.changeLanguage(locale);
        for (const key of keys) {
            fieldErrors.archive_visible_days = key;
            const html = render();
            assert.ok(html.includes(i18n.t(key)), `${locale}: message missing`);
            assert.ok(!html.includes("project.errors."), `${locale}: untranslated validation`);
        }
    }
});
test("normal archive field retains its value and visibility help", () => {
    delete fieldErrors.archive_visible_days;
    const html = render();
    assert.ok(html.includes('value="3"'));
    assert.ok(!html.includes("project.errors."));
});
