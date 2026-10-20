import { expect, test } from "@playwright/test";
test("authenticated bootstrap loads notifications once through the header", async ({ page }) => {
    await page.goto("/src/core/providers/NotificationLoad.fixture.html");
    await expect(page.locator("#state")).toHaveText("loaded");
    await expect(page.locator("#reads")).toHaveText("1");
});

for (const width of [390, 1457]) {
    test(`notification header keeps its title on one line at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 879 });
        await page.goto("/src/core/providers/NotificationLoad.fixture.html?lang=ko");
        await expect(page.locator("#state")).toHaveText("loaded");
        await page.getByRole("button", { name: "알림", exact: true }).click();
        const surface = page.locator("[data-notification-surface=content]");
        const title = surface.getByText("알림", { exact: true });
        await expect(title).toBeVisible();
        const geometry = await title.evaluate((el) => {
            const box = el.getBoundingClientRect();
            return { height: box.height, lineHeight: Number.parseFloat(getComputedStyle(el).lineHeight) };
        });
        expect(geometry.height).toBeLessThanOrEqual(geometry.lineHeight + 1);
        const bounds = await surface.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        await expect(surface.getByRole("switch")).toBeVisible();
        await surface.screenshot({ path: `../../.verification/ui-feedback-20261020/notifications-${width}.png` });
    });
}
