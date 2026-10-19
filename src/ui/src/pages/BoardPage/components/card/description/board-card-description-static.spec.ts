import { test, expect } from "@playwright/test";

test("static description chunks render internal links without runtime Plate hooks", async ({ page }, testInfo) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/src/pages/BoardPage/components/card/description/board-card-description-static.fixture.html");
    await expect(page.locator("[data-card-description-chunk]")).toBeVisible();
    await expect(page.locator(".internal-link")).toHaveText(/Linked fixture card/);
    await expect(page.locator("a").filter({ hasText: "https://example.com/fixture" })).toBeVisible();
    await expect(page.getByText("Static AI preview", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Linked fixture card", exact: true }).click();
    await expect(page.locator("[data-location]")).toHaveText("/board/fixture-project/fixture-card");

    expect(pageErrors).not.toContain("Plate hooks must be used inside a Plate or PlateController");
    expect(pageErrors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("static-description.png") });
});

for (const width of [390, 768, 1440]) {
    test(`protected image preview escapes transformed cards at ${width}px and preserves dismissal`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 });
        await page.addInitScript(() => localStorage.setItem("lang", "en-US"));
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto("/src/pages/BoardPage/components/card/description/board-card-description-static.fixture.html?preview=1");
        const opener = page.getByRole("button", { name: "Open preview", exact: true });
        const dialog = page.getByRole("dialog", { name: "Image", exact: true });
        await opener.click();
        await expect(dialog).toBeVisible();
        const bounds = await dialog.boundingBox();
        expect(bounds).toEqual({ x: 0, y: 0, width, height: 900 });
        const image = dialog.locator("img");
        await expect(image).toHaveAttribute("src", /^blob:/);
        await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBe(800);
        const imageBounds = await image.boundingBox();
        expect(imageBounds!.x).toBeGreaterThanOrEqual(0);
        expect(imageBounds!.x + imageBounds!.width).toBeLessThanOrEqual(width);
        await page.screenshot({ path: testInfo.outputPath(`protected-preview-${width}.png`) });
        await dialog.getByRole("button", { name: "Close", exact: true }).click();
        await expect(dialog).toHaveCount(0);
        await expect(opener).toBeVisible();
        await opener.click();
        await expect(dialog).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
        await expect(opener).toBeVisible();
        await opener.click();
        await expect(dialog).toBeVisible();
        await dialog.click({ position: { x: width - 4, y: 896 } });
        await expect(dialog).toHaveCount(0);
        expect(errors).toEqual([]);
    });
}
