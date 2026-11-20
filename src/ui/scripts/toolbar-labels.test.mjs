import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { inflateSync } from "node:zlib";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const packet = process.env.LANGBOARD_TOOLBAR_LABELS_BASE64
    ? JSON.parse(inflateSync(Buffer.from(process.env.LANGBOARD_TOOLBAR_LABELS_BASE64, "base64")).toString())
    : null;
const ui = process.env.LANGBOARD_UI_ROOT ? pathToFileURL(`${process.env.LANGBOARD_UI_ROOT}/`) : new URL("../", import.meta.url);
function load(path) {
    const exports = {};
    const source = packet?.sources[path] ?? readFileSync(new URL(path, ui), "utf8");
    runInNewContext(
        ts.transpileModule(source, {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
        }).outputText,
        {
            exports,
            require: (name) => {
                if (name === "@/components/base/Tooltip") return { __esModule: true, default: load("src/components/base/Tooltip/index.tsx").default };
                if (name === "@/core/utils/ComponentUtils") return { cn: (...values) => values.filter(Boolean).join(" ") };
                // These exports are not used by the exercised toolbar buttons.
                if (name === "@/components/base/DropdownMenu" || name === "@/components/base/Separator") return { __esModule: true, default: {} };
                return require(name);
            },
        }
    );
    return exports;
}
const { Toolbar, ToolbarButton } = load("src/components/plate-ui/toolbar.tsx");
const icon = React.createElement("svg", { "aria-hidden": true });
const render = (props, child = icon) => renderToStaticMarkup(React.createElement(Toolbar, null, React.createElement(ToolbarButton, props, child)));

test("translated tooltip names both native toolbar buttons and toggle items before hover", () => {
    for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
        const resource = packet?.resources[locale] ?? JSON.parse(readFileSync(new URL(`src/assets/locales/${locale}/editor.json`, ui), "utf8"));
        for (const pressed of [undefined, false, true]) {
            const html = render({ tooltip: resource["Bold (⌘+B)"], pressed });
            assert.ok(html.includes(`aria-label="${resource["Bold (⌘+B)"]}"`), `${locale}/${pressed}`);
            if (pressed !== undefined) assert.ok(html.includes('role="radio"'));
        }
    }
});

test("explicit text labels and referenced labels retain precedence over descriptive tooltips", () => {
    const text = render({ tooltip: "Turn into", "aria-label": "Paragraph", pressed: false }, "Paragraph");
    assert.ok(text.includes('aria-label="Paragraph"'));
    assert.ok(text.includes("Paragraph</button>"));
    const referenced = render({ tooltip: "Bold", "aria-labelledby": "existing-label", disabled: true });
    assert.ok(referenced.includes('aria-labelledby="existing-label"'));
    assert.ok(!referenced.includes("aria-label="));
    assert.ok(referenced.includes('disabled=""'));
});

test("rich tooltip content and untitled children are not coerced into accessible names", () => {
    const rich = render({ tooltip: React.createElement("span", null, "Explanation") }, "Action");
    assert.ok(!rich.includes("aria-label="));
    assert.ok(rich.includes("Action</button>"));
    const untitled = render({}, React.createElement("span", { className: "sr-only" }, "Manual action"));
    assert.ok(untitled.includes("Manual action"));
    assert.ok(!untitled.includes("aria-label="));
});
