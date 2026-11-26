import { test, expect } from "@playwright/test";

for (const width of [390, 1440]) {
    test(`native numbered renderer and draft round-trip at ${width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/components/Editor/plugins/markdown/list-number.fixture.html");
        await expect(page.locator("ol")).toHaveCount(5);
        const markers = () => page.locator("ol").evaluateAll((lists) => lists.map((list) => Number(list.getAttribute("start") ?? 1)));
        expect(await markers()).toEqual([1, 9, 2, 1, 2]);
        const markerRoom = await page.locator("ol").evaluateAll((lists) => {
            const editor = document.querySelector("[data-slate-editor]")!;
            return lists.map((list) => {
                const item = list.querySelector("li")!;
                return { left: item.getBoundingClientRect().left - editor.getBoundingClientRect().left, style: getComputedStyle(list).listStyleType };
            });
        });
        expect(markerRoom.every((item) => item.style === "decimal" && item.left >= 20)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath("numbered-renderer.png") });
        await page.getByRole("button", { name: "Save draft" }).click();
        await expect(page.locator("[data-saved-draft]")).toHaveText("1. One\n9. Nine\n2. Two\n1. Again\n2. Later\n");
        await page.getByRole("button", { name: "Reload draft" }).click();
        expect(await markers()).toEqual([1, 9, 2, 1, 2]);
        await page.getByRole("button", { name: "Edit draft" }).click();
        const content = page.getByRole("textbox", { name: "Numbered content" });
        await expect(content).toBeEditable();
        await page.getByText("Later", { exact: true }).click();
        await content.press("End");
        await content.press("Enter");
        await content.press("Enter");
        await content.pressSequentially("9. New");
        await page.getByRole("button", { name: "Save draft" }).click();
        await expect(page.locator("[data-saved-draft]")).toContainText("9. New");
        await page.getByRole("button", { name: "Reload draft" }).click();
        expect(await markers()).toEqual([1, 9, 2, 1, 2, 9]);
    });
}

test("literal slash text survives typing, undo and markdown round-trip", async ({ page }) => {
    await page.goto("/src/components/Editor/plugins/markdown/list-number.fixture.html?empty");
    const editor = page.getByRole("textbox", { name: "Numbered content" });
    await page.getByRole("button", { name: "Edit draft" }).click();
    await expect(editor).toBeEditable();
    const literal = "11/25 2026/11/25 12/31 1/2 11 / 25 docs/1/2 https://example.com";
    await editor.pressSequentially(literal);
    await expect(editor).toHaveText(literal);
    await editor.press("ControlOrMeta+z");
    await expect(editor).not.toHaveText(literal);
    await editor.press("ControlOrMeta+Shift+z");
    await expect(editor).toHaveText(literal);
    await page.getByRole("button", { name: "Save draft" }).click();
    const saved = page.locator("[data-saved-draft]");
    await expect(saved).toContainText("11/25 2026/11/25 12/31 1/2 11 / 25 docs/1/2");
    await expect(saved).toContainText(/https\\?:\/\/example\.com/);
    await page.getByRole("button", { name: "Reload draft" }).click();
    await expect(editor).toHaveText(literal);
    await editor.press("End");
    await editor.press("Space");
    await editor.pressSequentially("a >= b");
    await expect(editor).toContainText("a ≥ b");
});
