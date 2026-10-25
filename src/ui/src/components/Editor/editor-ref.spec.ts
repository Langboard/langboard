import { expect, test } from "@playwright/test";

for (const width of [1280, 390]) {
    test(`optional editor ref changes preserve input and hooks at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto("/src/components/Editor/editor-ref.fixture.html");
        const editor = page.locator("[contenteditable=true]");
        await expect(editor).toContainText("Original content");
        await editor.press("End");
        await editor.pressSequentially(" local draft");
        for (const mode of ["external", "internal", "external"]) {
            await page.getByRole("button", { name: "Toggle external ref" }).click();
            await expect(page.getByTestId("ref-mode")).toHaveText(mode);
            await expect(editor).toContainText("local draft");
        }
        expect(errors).toEqual([]);
    });
}

const original = "First paragraph with **bold text**.\n\nSecond paragraph with the click caret.\n\nThird paragraph remains.";
const isMac = process.platform === "darwin";
const replacement = "Replacement first paragraph.\n\nReplacement second paragraph.";

for (const shortcut of ["Meta+A", "Control+A", "ControlOrMeta+A"]) {
    test(`native clipboard replacement with ${shortcut}`, async ({ page, context }) => {
        await context.grantPermissions(["clipboard-read", "clipboard-write"]);
        await page.goto(`/src/components/Editor/editor-ref.fixture.html?content=${encodeURIComponent(original)}`);
        const editor = page.locator("[data-slate-editor]");
        await page.getByRole("button", { name: "Toggle external ref" }).click();
        await expect(editor.locator("[data-slate-node=element]")).toHaveCount(3);
        await editor.locator("[data-slate-node=element]").nth(1).click();
        await expect(editor).toBeFocused();
        await expect.poll(() => page.evaluate(() => window.editorFixtureSelection?.anchor.path[0])).toBe(1);
        // Deliver native keys to the focused editor; locator.press may refocus and restore a stale caret.
        await page.keyboard.press(shortcut);
        const selection = await page.evaluate(() => window.getSelection()?.toString());
        await page.evaluate((text) => navigator.clipboard.writeText(text), replacement);
        await page.keyboard.press("ControlOrMeta+V");
        if (shortcut === (isMac ? "Control+A" : "Meta+A")) {
            expect(selection).toBe("");
            await expect(editor).toContainText("First paragraph with bold text.");
            await expect(editor).toContainText("Third paragraph remains.");
            await expect(editor).toContainText("Replacement first paragraph.");
        } else {
            await expect(editor.locator("[data-slate-node=element]")).toHaveText(replacement.split("\n\n"));
            expect(await page.getByTestId("content").textContent()).toBe(`${replacement}\n`);
        }
    });
}

test("native document start paste prepends and retains rich text", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(`/src/components/Editor/editor-ref.fixture.html?content=${encodeURIComponent(original)}`);
    const editor = page.locator("[data-slate-editor]");
    await page.getByRole("button", { name: "Toggle external ref" }).click();
    await editor.locator("[data-slate-node=element]").nth(1).click();
    await expect(editor).toBeFocused();
    await expect.poll(() => page.evaluate(() => window.editorFixtureSelection?.anchor.path[0])).toBe(1);
    await page.keyboard.press(isMac ? "Meta+ArrowUp" : "Control+Home");
    await expect
        .poll(() =>
            page.evaluate(() => {
                const selection = window.getSelection();
                return { text: selection?.anchorNode?.textContent, offset: selection?.anchorOffset };
            })
        )
        .toEqual({ text: "First paragraph with ", offset: 0 });
    await page.evaluate(() => navigator.clipboard.writeText("Prefix:"));
    await page.keyboard.press("ControlOrMeta+V");
    await expect(editor.locator("[data-slate-node=element]").first()).toHaveText("Prefix:First paragraph with bold text.");
    await expect(editor.locator("strong")).toHaveText("bold text");
    expect(await page.getByTestId("content").textContent()).toBe(`Prefix:${original}\n`);
});
