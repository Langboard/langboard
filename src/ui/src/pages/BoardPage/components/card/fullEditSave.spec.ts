import { expect, test, type Page, type Route } from "@playwright/test";
import { createServer, transformWithEsbuild, type ViteDevServer } from "vite";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Production provider, sections, navigation, section-save and HTTP controller.
// Only transport/editor widgets, picker and unrelated flip tray are substituted.
const boundaries: Record<string, string> = {
    "@/components/Collaborative/useCollaborativeText": `
        import { useCallback, useRef } from 'react';
        export function useCollaborativeText(props) {
            const latest = useRef(props); latest.current = props;
            const updateValue = useCallback(value => latest.current.onValueChange?.(value), []);
            const updateMeta = useCallback(() => {}, []);
            return { isSynced: !location.search.includes('unsynced'), updateValue, resetValue: updateValue, updateMeta };
        }`,
    "@/components/Collaborative": `
        import { useState, useEffect } from 'react';
        function Textarea(props) {
            const [value, setValue] = useState(props.defaultValue);
            useEffect(() => {
                props.onCollaborativeValueReady?.(setValue);
                props.onCollaborativeValueResetReady?.(setValue);
                return () => { props.onCollaborativeValueReady?.(null); props.onCollaborativeValueResetReady?.(null); };
            }, []);
            return <textarea aria-label="Title draft" ref={props.ref} value={value}
                onChange={event => { setValue(event.target.value); props.onValueChange(event.target.value); }} />;
        }
        export default { Textarea };`,
    "@/components/base/DateTimePicker": `
        export default function Picker({ value, disabled, onChange }) {
            return <input aria-label="Deadline draft" type="datetime-local" disabled={disabled}
                value={value ? value.toISOString().slice(0,16) : ''}
                onChange={event => onChange(event.target.value ? new Date(event.target.value + ':00Z') : undefined)} />;
        }`,
    "@/components/Editor/plate-editor": `
        import { useEffect, useRef, useState } from 'react';
        export function PlateEditor(props) {
            const [value, setValue] = useState(props.value?.content ?? '');
            const content = useRef(value);
            const editor = useRef({ api: { markdown: { serialize: () => content.current } }, getTransforms: () => ({}), getApi: () => ({}) });
            useEffect(() => {
                props.editorRef.current = editor.current;
                props.onCollaborativeValueReady?.(() => {});
                props.onCollaborativeValueResetReady?.(value => { content.current = value; setValue(value); });
                return () => { props.editorRef.current = null; props.onCollaborativeValueReady?.(null); };
            }, []);
            return <textarea aria-label="Description draft" value={value} onChange={event => {
                content.current = event.target.value; setValue(event.target.value); props.onEditorChange?.(editor.current);
            }} />;
        }`,
    "./CardFlipTray": "export default function Tray() { return null; }",
};
let server: ViteDevServer;
test.beforeAll(async () => {
    // Vite runner avoids writing bundled config into the full external volume.
    // The existing base config uses __dirname; provide it only during loading.
    const previousDirname = Object.getOwnPropertyDescriptor(globalThis, "__dirname");
    Object.defineProperty(globalThis, "__dirname", { configurable: true, value: fileURLToPath(new URL("../../../../../", import.meta.url)) });
    server = await createServer({
        configLoader: "runner",
        configFile: fileURLToPath(new URL("../../../../../vite.card-load.config.ts", import.meta.url)),
        root: fileURLToPath(new URL("../../../../../", import.meta.url)),
        // Optimized dependencies must retain node_modules' source-transform exclusion.
        cacheDir: join(tmpdir(), "node_modules", "langboard-full-edit-cache"),
        define: { "process.env.API_URL": JSON.stringify("http://127.0.0.1:4297") },
        server: { host: "127.0.0.1", port: 4297, strictPort: true },
        plugins: [
            {
                name: "full-edit-fixture-boundaries",
                enforce: "pre",
                resolveId(id) {
                    const root = fileURLToPath(new URL("../../../../../src/", import.meta.url));
                    const source = id.startsWith(root) ? "@/" + id.slice(root.length) : id;
                    return source in boundaries ? `\0full-edit:${source}.jsx` : undefined;
                },
                async load(id) {
                    if (!id.startsWith("\0full-edit:")) return;
                    return transformWithEsbuild(boundaries[id.slice("\0full-edit:".length, -4)], id, { loader: "jsx", jsx: "automatic" });
                },
                transform(code, id) {
                    if (process.env.FULL_EDIT_BASELINE === "1" && id.endsWith("/BoardCard.tsx")) {
                        return code.replace(
                            /if \(getSectionDraftSnapshot\(\) === draftSnapshot\) \{\s*leaveCardEditMode\(session\);\s*\}/,
                            "leaveCardEditMode();"
                        );
                    }
                },
            },
        ],
    });
    await server.listen();
    if (previousDirname) Object.defineProperty(globalThis, "__dirname", previousDirname);
    else Reflect.deleteProperty(globalThis, "__dirname");
});
test.afterAll(async () => server?.close());
async function open(page: Page, query = "") {
    page.on("pageerror", (error) => console.log("Fixture page error:", error.message));
    page.on("console", (message) => {
        if (message.type() === "error") console.log("Fixture console:", message.text());
    });
    await page.goto(`http://127.0.0.1:4297/src/pages/BoardPage/components/card/fullEditSave.fixture.html${query}`);
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Description draft")).toBeVisible();
}
async function holdSave(page: Page) {
    let release!: () => void;
    let arrived!: (route: Route) => void;
    const pending = new Promise<Route>((resolve) => (arrived = resolve));
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/card/**", async (route) => {
        if (route.request().method() !== "PUT") return route.fulfill({ json: {} });
        arrived(route);
        await gate;
        await route.fulfill({ json: {} });
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const route = await pending;
    return { release, body: route.request().postDataJSON() };
}
test("delayed success preserves newer deadline and can submit it afterward", async ({ page }) => {
    await open(page);
    const { release, body } = await holdSave(page);
    expect(body.deadline_at).toBe("2030-06-15T12:00:00.000Z");
    await page.getByLabel("Deadline draft").fill("2030-06-16T13:00");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Deadline draft")).toHaveValue("2030-06-16T13:00");
    await page.unroute("**/card/**");
    await page.route("**/card/**", (route) => route.fulfill({ json: {} }));
    const next = page.waitForRequest((request) => request.method() === "PUT");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    expect((await next).postDataJSON().deadline_at).toBe("2030-06-16T13:00:00.000Z");
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
});
test("pending save preserves newer title", async ({ page }) => {
    await open(page);
    await page.getByText("Original title", { exact: true }).click();
    await page.getByLabel("Title draft").fill("Submitted title");
    const { release, body } = await holdSave(page);
    expect(body.title).toBe("Submitted title");
    await page.getByLabel("Title draft").fill("Newer title");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Title draft")).toHaveValue("Newer title");
    await expect(page.getByTestId("mode")).toHaveText("edit");
});
test("pending save preserves description before its debounce flush", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Submitted description");
    const { release, body } = await holdSave(page);
    expect(body.description.content).toBe("Submitted description");
    await page.getByLabel("Description draft").fill("Newer description");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Description draft")).toHaveValue("Newer description");
    await expect(page.getByTestId("body-draft")).toHaveText("Newer description");
});
test("Cancel and same-card re-entry preserve a replacement with identical deadline", async ({ page }) => {
    await open(page);
    const { release } = await holdSave(page);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByText("No deadline", { exact: true }).click();
    await page.getByLabel("Deadline draft").fill("2030-06-15T12:00");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Deadline draft")).toHaveValue("2030-06-15T12:00");
});
test("failure retains drafts; successful retry clears them", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Recoverable description");
    await page.route("**/card/**", (route) => route.fulfill({ status: 500, json: { detail: "Synthetic failure" } }));
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Description draft")).toHaveValue("Recoverable description");
    await expect(page.getByTestId("body-draft")).toHaveText("Recoverable description");
    await page.unroute("**/card/**");
    await page.route("**/card/**", (route) => route.fulfill({ json: {} }));
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
    await expect(page.getByTestId("body-draft")).toHaveText("empty");
});
test("unsynchronized draft blocks PUT and preserves Cancel", async ({ page }) => {
    await open(page, "?unsynced");
    let writes = 0;
    await page.route("**/card/**", (route) => {
        if (route.request().method() === "PUT") writes++;
        return route.fulfill({ json: {} });
    });
    await expect(page.getByLabel("Deadline draft")).toBeDisabled();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    expect(writes).toBe(0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByLabel("Deadline draft")).toHaveValue("");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
});

test("normal unchanged sections and whitespace title finish without PUT", async ({ page }) => {
    await open(page, "?unchanged");
    await page.getByText("Original title", { exact: true }).click();
    await page.getByLabel("Title draft").fill("  Original title  ");
    let writes = 0;
    await page.route("**/card/**", (route) => {
        if (route.request().method() === "PUT") writes++;
        return route.fulfill({ json: {} });
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    expect(writes).toBe(0);
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
});

test("Cancel during pending save remains cancelled after completion", async ({ page }) => {
    await open(page);
    const { release } = await holdSave(page);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    release();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
    await expect(page.getByTestId("body-draft")).toHaveText("empty");
});
