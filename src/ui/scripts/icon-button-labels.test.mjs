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
const packet = process.env.LANGBOARD_ICON_LABELS_BASE64
    ? JSON.parse(inflateSync(Buffer.from(process.env.LANGBOARD_ICON_LABELS_BASE64, "base64")).toString())
    : null;
const ui = process.env.LANGBOARD_UI_ROOT ? pathToFileURL(`${process.env.LANGBOARD_UI_ROOT}/`) : new URL("../", import.meta.url);
const source = packet?.source ?? readFileSync(new URL("src/components/base/Button/index.tsx", ui), "utf8");
const exports = {};
runInNewContext(
    ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText,
    {
        exports,
        require: (name) => {
            // Hide tooltip content as in its closed state. Native button and Radix Slot remain real.
            if (name === "@/components/base/Tooltip")
                return { __esModule: true, default: { Root: ({ children }) => children, Trigger: ({ children }) => children, Content: () => null } };
            if (name === "@/core/utils/ComponentUtils") return { cn: (...values) => values.filter(Boolean).join(" ") };
            return require(name);
        },
    }
);
const Button = exports.default;
const icon = React.createElement("svg", { "aria-hidden": true });
const render = (props, child = icon) => renderToStaticMarkup(React.createElement(Button, props, child));

test("icon variants expose the existing translated tooltip as their accessible name", () => {
    for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
        const resources = packet?.resources[locale] ?? JSON.parse(readFileSync(new URL(`src/assets/locales/${locale}/common.json`, ui), "utf8"));
        for (const size of ["icon", "icon-sm"]) {
            const html = render({ size, title: resources.Refresh, type: "button" });
            assert.ok(html.includes(`aria-label="${resources.Refresh}"`), `${locale}/${size}`);
            assert.ok(html.includes('type="button"'));
        }
    }
});

test("explicit accessible labels and label references retain precedence", () => {
    const labelled = render({ size: "icon", title: "Refresh", "aria-label": "Custom action", disabled: true });
    assert.ok(labelled.includes('aria-label="Custom action"'));
    assert.ok(labelled.includes('disabled=""'));
    const referenced = render({ size: "icon", title: "Refresh", "aria-labelledby": "existing-label" });
    assert.ok(referenced.includes('aria-labelledby="existing-label"'));
    assert.ok(!referenced.includes("aria-label="));
});

test("text labels, untitled icons and asChild labels are preserved", () => {
    const text = render({ title: "Only administrators can save" }, "Save");
    assert.ok(text.includes(">Save</button>"));
    assert.ok(!text.includes("aria-label="));
    const untitled = render({ size: "icon" }, React.createElement("span", { className: "sr-only" }, "Manual action"));
    assert.ok(untitled.includes("Manual action"));
    assert.ok(!untitled.includes("aria-label="));
    const child = render(
        { size: "icon", title: "Refresh", asChild: true },
        React.createElement("a", { href: "#qa", "aria-label": "Child action" }, icon)
    );
    assert.ok(child.includes('aria-label="Child action"'));
    assert.ok(child.includes('href="#qa"'));
});
