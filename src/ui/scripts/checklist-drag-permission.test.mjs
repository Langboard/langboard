/* global URL, document, process, window */
/* eslint quotes: off, @/max-len: off */
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { build } from "esbuild";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("..", import.meta.url));
const pass = 'import React from "react"; const Pass = p => React.createElement(React.Fragment, null, p.children);';
const stubs = {
    "@/components/base/Box":
        'import React from "react"; export default React.forwardRef((p, r) => React.createElement("div", { ref: r }, p.children));',
    "@/components/base/Flex": 'import React from "react"; export default p => React.createElement("div", null, p.children);',
    "@/components/base/Button":
        'import React from "react"; export default React.forwardRef((p, r) => React.createElement("button", { ref: r, title: p.title, disabled: p.disabled }, p.children));',
    "@/components/base/IconComponent": 'export default () => "drag";',
    "@/components/base/Tooltip": `${pass} export default { Root: Pass, Trigger: Pass, Content: () => null };`,
    "@/components/base/Collapsible": `${pass} export default { Root: Pass, Trigger: Pass, Content: Pass };`,
    "@/core/models/ModelRegistry": `${pass} export const ModelRegistry = { ProjectCheckitem: { Provider: Pass }, ProjectChecklist: { Provider: Pass } };`,
    "@/core/providers/BoardCardProvider": `import React from "react";
        export const Ctx = React.createContext(false);
        const viewportRef = { current: document.body };
        export function useBoardCard() {
            const allowed = React.useContext(Ctx);
            return { projectUID: "p", card: { uid: "c" }, currentUser: { uid: "u" }, viewportRef,
                hasRoleAction: () => allowed, sharedClassNames: { popoverContent: "" }, socket: {} };
        }`,
    "@/core/hooks/usePageNavigate": "export const usePageNavigateRef = () => () => {};",
    "@/core/hooks/useRowReordered": "export default () => ({ rows: [] });",
    "react-i18next": "export const useTranslation = () => [value => value];",
};
for (const name of ["AssignedMember", "Checkbox", "MoreMenu", "Timer"]) {
    stubs[`@/pages/BoardPage/components/card/checklist/BoardCardCheckitem${name}`] = "export default () => null;";
}
for (const name of ["AddItem", "Checkbox", "MoreMenu", "Notify"]) {
    stubs[`@/pages/BoardPage/components/card/checklist/BoardCardChecklist${name}`] = "export default () => null;";
}

async function fixture(kind) {
    const component = kind === "item" ? "BoardCardCheckitem" : "BoardCardChecklist";
    const props = kind === "item" ? "{ checkitem: model }" : "{ checklist: model, checkitemsMap: {} }";
    const source = `import React from "react";
        import { createRoot } from "react-dom/client";
        import "@/core/injection/TypeExtensions";
        import { Ctx } from "@/core/providers/BoardCardProvider";
        import Component from "@/pages/BoardPage/components/card/checklist/${component}";
        const model = { uid: "a", title: "Example", order: 0, checklist_uid: "list", is_checked: false,
            isOpenedInBoardCard: true, useField(key) { return this[key]; },
            useForeignFieldOne() { return null; }, useForeignFieldArray() { return []; } };
        function App() {
            const [allowed, set] = React.useState(false);
            return React.createElement(Ctx.Provider, { value: allowed },
                React.createElement("button", { id: "grant", onClick: () => set(true) }, "grant"),
                React.createElement("button", { id: "revoke", onClick: () => set(false) }, "revoke"),
                React.createElement(Component, ${props}));
        }
        createRoot(document.body).render(React.createElement(App));`;
    const result = await build({
        stdin: { contents: source, resolveDir: root, loader: "tsx" },
        absWorkingDir: root,
        bundle: true,
        write: false,
        platform: "browser",
        format: "iife",
        jsx: "automatic",
        loader: { ".css": "empty" },
        plugins: [
            {
                name: "permission-fixture",
                setup(builder) {
                    builder.onResolve({ filter: /.*/ }, ({ path, kind, pluginData }) => {
                        if (path in stubs) return { path, namespace: "permission-fixture" };
                        if (!pluginData?.resolved && path.startsWith("@/")) {
                            return builder.resolve(join(root, "src", path.slice(2)), { kind, resolveDir: root, pluginData: { resolved: true } });
                        }
                    });
                    builder.onLoad({ filter: /.*/, namespace: "permission-fixture" }, ({ path }) => ({
                        contents: stubs[path],
                        loader: "jsx",
                        resolveDir: root,
                    }));
                },
            },
        ],
    });
    return result.outputFiles.find((file) => file.path.endsWith(".js") || file.path === "<stdout>").text;
}

for (const kind of ["item", "checklist"]) {
    test(`${kind} drag registration follows permission changes`, async () => {
        const script = await fixture(kind);
        const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
        try {
            const page = await browser.newPage();
            page.setDefaultTimeout(2500);
            const errors = [];
            page.on("pageerror", (error) => errors.push(error.message));
            await page.addScriptTag({ content: script });
            const handle = page.getByTitle("common.Drag to reorder");
            assert.equal(await handle.count(), 0);
            await page.locator("#grant").click();
            await handle.waitFor();
            await page.waitForFunction(() => document.querySelector('[title="common.Drag to reorder"]')?.getAttribute("draggable") === "true");
            await handle.evaluate((element) => {
                window.previousHandle = element;
            });
            await page.locator("#revoke").click();
            assert.equal(await handle.count(), 0);
            assert.equal(await page.evaluate(() => window.previousHandle.getAttribute("draggable")), null);
            await page.locator("#grant").click();
            await handle.waitFor();
            await page.waitForFunction(() => document.querySelector('[title="common.Drag to reorder"]')?.getAttribute("draggable") === "true");
            assert.deepEqual(errors, []);
        } finally {
            await browser.close();
        }
    });
}
