import { expect, test } from "@playwright/test";
for (const width of [1280, 390]) {
    test(`delayed server snapshot preserves unsaved Yjs draft at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(`/src/components/Editor/collaborative-draft.fixture.html?card=draft-${width}`);
        await expect(page.getByTestId("ready")).toHaveText("ready");
        const editor = page.locator("[contenteditable=true]");
        await expect(editor).toContainText("Initial server content");
        await editor.press("End");
        await editor.pressSequentially(" local unsaved draft");
        await page.getByRole("button", { name: "Delayed server update" }).click();
        await expect(editor).toContainText("local unsaved draft");
        await expect(editor).not.toContainText("Delayed server content");
        await page.getByRole("button", { name: "Explicit reset", exact: true }).click();
        await expect(editor).toContainText("Explicit restored content");
        expect(errors).toEqual([]);
    });
}

for (const width of [1280, 390]) {
    test(`unsynchronized draft cannot accept keyboard edits at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.routeWebSocket(/127\.0\.0\.1:4243/, () => {});
        await page.goto(`/src/components/Editor/collaborative-draft.fixture.html?card=waiting-${width}`);
        await expect(page.getByTestId("ready")).toHaveText("waiting");
        const editor = page.locator("[data-slate-editor]");
        await expect(editor).toContainText("Initial server content");
        await expect(editor).toHaveAttribute("contenteditable", "false");
        await editor.press("Tab");
        await page.keyboard.type("must not enter the unsynchronized draft");
        await expect(editor).not.toContainText("must not enter");
    });
}
