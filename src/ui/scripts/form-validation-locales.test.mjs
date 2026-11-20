import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import console from "node:console";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createInstance } from "i18next";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { I18nextProvider } = require("react-i18next");
const here = dirname(fileURLToPath(import.meta.url));
const filename = resolve(here, "../src/components/base/Form/index.tsx");
const exportsObject = {};
runInNewContext(
    ts.transpileModule(readFileSync(filename, "utf8"), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText,
    { exports: exportsObject, require, console },
    { filename }
);
const Form = exportsObject.default;
const resources = Object.fromEntries(
    ["en-US", "ko-KR", "ja-JP", "zh-CN"].map((locale) => [
        locale,
        {
            translation: {
                errors: JSON.parse(readFileSync(resolve(here, `../src/assets/locales/${locale}/errors.json`), "utf8")),
                myAccount: JSON.parse(readFileSync(resolve(here, `../src/assets/locales/${locale}/myAccount.json`), "utf8")),
            },
        },
    ])
);

test("field validation preserves specific messages and falls back for unregistered field errors", async () => {
    const i18n = createInstance();
    await i18n.init({ resources, lng: "en-US", fallbackLng: "en-US" });
    const utilsFile = resolve(here, "../src/core/hooks/form/utils.ts");
    const utils = {};
    runInNewContext(
        ts.transpileModule(readFileSync(utilsFile, "utf8"), {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        }).outputText,
        { exports: utils, require: (name) => (name === "i18next" ? i18n : require(name)) },
        { filename: utilsFile }
    );
    for (const [locale, { translation }] of Object.entries(resources)) {
        await i18n.changeLanguage(locale);
        const prefix = "myAccount.errors";
        assert.equal(i18n.t(utils.convertValidationToLangKey(prefix, "required", "firstname")), translation.myAccount.errors.missing.firstname);
        assert.equal(i18n.t(utils.convertValidationToLangKey(prefix, "mimeType", "avatar")), translation.errors.form.invalid);
        const keys = utils.handleResponseErrors(prefix, { missing: { body: ["avatar"] }, value_error: { body: ["revision"] } });
        assert.equal(i18n.t(keys.avatar), translation.errors.form.valueMissing);
        assert.equal(i18n.t(keys.revision), translation.errors.form.invalid);
        const malformed = utils.handleResponseErrors(prefix, {
            missing: { body: "invalid shape" },
            value_error: { body: [null, 9, {}, "", "revision"] },
        });
        assert.deepEqual(Object.keys(malformed), ["revision"]);
        assert.equal(i18n.t(malformed.revision), translation.errors.form.invalid);
    }
});

test("production Form translates built-in validation in four locales and preserves caller content", async () => {
    const i18n = createInstance();
    await i18n.init({ resources, lng: "en-US", fallbackLng: "en-US", interpolation: { escapeValue: false } });
    const render = (props) =>
        renderToStaticMarkup(
            React.createElement(
                I18nextProvider,
                { i18n },
                React.createElement(Form.Root, null, React.createElement(Form.Field, { name: "value" }, React.createElement(Form.Message, props)))
            )
        );
    for (const [locale, resource] of Object.entries(resources)) {
        await i18n.changeLanguage(locale);
        const messages = resource.translation.errors.form;
        assert(render({}).includes(messages.invalid), locale);
        assert(render({ match: "badInput", forceMatch: true }).includes(messages.invalid), `${locale}/badInput`);
        for (const [match, message] of Object.entries(messages).filter(([key]) => key !== "invalid")) {
            const html = render({ match, forceMatch: true });
            assert(html.includes(message), `${locale}/${match}`);
            assert(!html.includes("errors.form."), `${locale}/${match}: raw translation key`);
        }
        assert(render({ match: "valueMissing", forceMatch: true, children: "Custom validation" }).includes("Custom validation"));
        assert(!render({ match: "valid", forceMatch: true }).includes("errors.form."));
        assert.equal(render({ match: "valueMissing" }), "<form><div></div></form>");
    }
});
