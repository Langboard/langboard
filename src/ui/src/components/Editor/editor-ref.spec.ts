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
