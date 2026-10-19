import { test, expect } from "@playwright/test";

const fixture = "/src/pages/BoardPage/components/card/description/description-scroll.fixture.html";
test.beforeEach(async ({ page }) => {
    await page.route(`**${fixture}`, async (route) => {
        const response = await route.fetch();
        await route.fulfill({ response, body: (await response.text()).replace("description-scroll.fixture.tsx", "overview-rail.fixture.tsx") });
    });
    await page.goto(fixture);
});
for (const width of [320, 390, 1440]) {
    test(`native overview geometry and interaction at ${width}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 });
        const rail = page.locator("[data-card-description-rail]");
        const viewport = page.locator("[data-card-content-viewport]");
        const scrollbar = page.getByRole("scrollbar");
        await expect(rail).toBeVisible();
        const initialThumb = await page.locator("[data-card-description-thumb]").boundingBox();
        expect(initialThumb!.height).toBe(20);
        await page.getByRole("button", { name: "Short", exact: true }).click();
        await expect(rail).toHaveCount(0);
        await page.screenshot({ path: testInfo.outputPath(`overview-rail-${width}-short.png`) });
        await page.getByRole("button", { name: "Medium", exact: true }).click();
        await expect(rail).toBeVisible();
        await expect
            .poll(async () => {
                const expected = await viewport.evaluate((element) => ((element.clientHeight - 48) * element.clientHeight) / element.scrollHeight);
                return Math.abs((await page.locator("[data-card-description-thumb]").boundingBox())!.height - expected);
            })
            .toBeLessThan(1);
        await page.screenshot({ path: testInfo.outputPath(`overview-rail-${width}-medium.png`) });
        await scrollbar.focus();
        await page.keyboard.press("End");
        await expect
            .poll(() => viewport.evaluate((element) => element.scrollTop))
            .toBe(await viewport.evaluate((element) => element.scrollHeight - element.clientHeight));
        await page.keyboard.press("Home");
        await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBe(0);
        const bounds = (await scrollbar.boundingBox())!;
        await page.mouse.move(bounds.x + 5, bounds.y + 20);
        await page.mouse.down();
        await page.mouse.move(bounds.x + 5, bounds.y + 130, { steps: 5 });
        await page.mouse.up();
        await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(100);
        await page.getByRole("button", { name: "Long", exact: true }).click();
        const markers = rail.locator("button");
        await markers.last().click();
        await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(8000);
        await markers.first().hover();
        await expect(page.getByText("Preview 1", { exact: true })).toBeVisible();
        await page.mouse.move(width - 10, 850);
        await expect(page.getByText("Preview 1", { exact: true })).toBeHidden();
        await page.getByRole("button", { name: "Resize", exact: true }).click();
        await expect.poll(async () => (await rail.boundingBox())!.height).toBe(await viewport.evaluate((element) => element.clientHeight - 48));
        await page.getByRole("button", { name: "Content resize", exact: true }).click();
        await expect
            .poll(() => scrollbar.getAttribute("aria-valuemax"))
            .toBe(await viewport.evaluate((element) => String(element.scrollHeight - element.clientHeight)));
        await page.getByRole("button", { name: "Theme", exact: true }).click();
        await page.emulateMedia({ reducedMotion: "reduce" });
        await scrollbar.focus();
        await page.keyboard.press("End");
        await expect
            .poll(() => viewport.evaluate((element) => element.scrollTop))
            .toBe(await viewport.evaluate((element) => element.scrollHeight - element.clientHeight));
        await expect
            .poll(async () => {
                const thumb = (await page.locator("[data-card-description-thumb]").boundingBox())!;
                const railBounds = (await rail.boundingBox())!;
                return Math.abs(thumb.y + thumb.height - railBounds.y - railBounds.height);
            })
            .toBeLessThan(1);
        await page.screenshot({ path: testInfo.outputPath(`overview-rail-${width}-long-dark.png`) });
    });
}
