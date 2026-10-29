import { expect, test } from "@playwright/test";
const fixture = "/src/pages/DashboardPage/components/TrackingRow.fixture.html";
test("populated production row updates its card title and both links use the authorized board", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => {
        errors.push(error.message);
        console.log(error.stack);
    });
    await page.goto(fixture);
    await expect(page.getByTestId("tracking-row")).toContainText("2m");
    await expect(page.getByRole("button", { name: "Authorized card", exact: true })).toBeVisible();
    await page.evaluate(() => window.trackingFixture.rename());
    await page.getByRole("button", { name: "Updated card", exact: true }).click();
    await expect(page).toHaveURL(/\/board\/tracking-project\/tracking-card$/);
    await page.goto(fixture);
    await page.getByRole("button", { name: "Tracked task", exact: true }).click();
    await expect(page).toHaveURL(/\/board\/tracking-project\/tracking-card$/);
    expect(errors).toEqual([]);
});
test("missing card suppresses navigation and late model creation restores production row", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => {
        errors.push(error.message);
        console.log(error.stack);
    });
    await page.goto(`${fixture}?mode=missing`);
    await page.waitForFunction(() => !!window.trackingFixture);
    await expect(page.getByTestId("tracking-row")).toHaveCount(0);
    await expect(page.getByRole("button")).toHaveCount(0);
    await page.evaluate(() => window.trackingFixture.restore());
    await expect(page.getByRole("button", { name: "Authorized card", exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});
test("removed card suppresses stale row and restoration retains timer and navigation", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => {
        errors.push(error.message);
        console.log(error.stack);
    });
    await page.goto(fixture);
    await expect(page.getByTestId("tracking-row")).toBeVisible();
    await page.evaluate(() => window.trackingFixture.remove());
    // A checkitem update also rerenders the existing implementation after model removal.
    await page.evaluate(() => window.trackingFixture.refresh());
    await expect(page.getByTestId("tracking-row")).toHaveCount(0);
    await expect(page.getByRole("button")).toHaveCount(0);
    await page.evaluate(() => window.trackingFixture.restore());
    await expect(page.getByTestId("tracking-row")).toContainText("2m");
    await page.getByRole("button", { name: "Updated task", exact: true }).click();
    await expect(page).toHaveURL(/\/board\/tracking-project\/tracking-card$/);
    expect(errors).toEqual([]);
});
test("empty tracking collection renders without rows or navigation", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => {
        errors.push(error.message);
        console.log(error.stack);
    });
    await page.goto(`${fixture}?mode=empty`);
    await page.waitForFunction(() => !!window.trackingFixture);
    await expect(page.getByTestId("tracking-row")).toHaveCount(0);
    await expect(page.getByRole("button")).toHaveCount(0);
    expect(errors).toEqual([]);
});

for (const width of [390, 1280]) {
    test(`completed row decoration preserves both pointer links at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        for (const name of ["Tracked task", "Authorized card"]) {
            await page.goto(`${fixture}?mode=completed`);
            const link = page.getByRole("button", { name, exact: true });
            await expect(link).toBeVisible();
            const position = await link.evaluate((element) => {
                const row = element.closest("[data-testid=tracking-row]")!;
                const rowBounds = row.getBoundingClientRect();
                const linkBounds = element.getBoundingClientRect();
                const line = getComputedStyle(row, "::after");
                return {
                    x: linkBounds.width / 2,
                    y: rowBounds.top + Number.parseFloat(line.top) - linkBounds.top,
                };
            });
            await link.click({ position });
            await expect(page).toHaveURL(/\/board\/tracking-project\/tracking-card$/);
        }
    });
}
