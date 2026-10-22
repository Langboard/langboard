import { expect, test } from "@playwright/test";
test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => {
        throw error;
    });
});
const path = "/src/controllers/api/board/board-automation.fixture.html";
for (const width of [390, 1440]) {
    test.describe(`native automation ${width}`, () => {
        test.use({ viewport: { width, height: 900 } });
        test("idle opt out and shared consumers", async ({ page }) => {
            await page.goto(path);
            await expect(page.locator("li").filter({ hasText: /\/cards\?include_automation=false$/ })).toHaveCount(1);
            await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(0);
            await page.getByRole("button", { name: "Open panel", exact: true }).click();
            await expect(page.getByText("Native bot", { exact: true }).first()).toBeVisible();
            await page.getByRole("button", { name: "Open shared panel" }).click();
            await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(1);
            await page.getByRole("button", { name: "Inspect models" }).click();
            await expect(page.locator("output")).toHaveText("Hydrated; other: retained");
        });
        test("column menu requests on open", async ({ page }) => {
            await page.goto(path);
            await expect(page.locator("li").filter({ hasText: /metadata/ })).toHaveCount(1);
            await page.getByRole("button", { name: "More", exact: true }).press("Enter");
            await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(1);
        });
        test("slow response shows loading without write controls", async ({ page }) => {
            await page.goto(`${path}?scenario=slow`);
            await page.getByRole("button", { name: "Open panel", exact: true }).click();
            await expect(page.locator("[role='status']")).toContainText("Loading");
            await expect(page.getByRole("button", { name: "Triggers", exact: true })).toHaveCount(0);
            await page.getByRole("button", { name: "Release", exact: true }).click();
            await expect(page.getByText("Native bot", { exact: true }).first()).toBeVisible();
        });
        for (const scenario of ["fail", "missing"]) {
            test(`${scenario} is visible with explicit retry`, async ({ page }) => {
                await page.goto(`${path}?scenario=${scenario}`);
                await page.getByRole("button", { name: "Open panel", exact: true }).click();
                await expect(page.getByRole("alert")).toBeVisible();
                await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(1);
                await page.getByRole("button", { name: "Retry", exact: true }).click();
                await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(2);
                if (scenario === "fail") await expect(page.getByText("Native bot", { exact: true }).first()).toBeVisible();
                else await expect(page.getByRole("alert")).toBeVisible();
            });
        }
        test("foreign board payload fails before model hydration", async ({ page }) => {
            await page.goto(`${path}?scenario=foreign`);
            await page.getByRole("button", { name: "Open panel", exact: true }).click();
            await expect(page.getByRole("alert")).toBeVisible();
            await page.getByRole("button", { name: "Inspect models" }).click();
            await expect(page.locator("output")).toHaveText("Unhydrated; other: retained");
        });
        test("column failure exposes retry without a false empty state", async ({ page }) => {
            await page.goto(`${path}?scenario=fail`);
            await expect(page.locator("li").filter({ hasText: /metadata/ })).toHaveCount(1);
            await page.getByRole("button", { name: "More", exact: true }).press("Enter");
            await expect(page.getByRole("alert")).toBeVisible();
            await page.getByRole("menuitem", { name: /Retry/ }).click();
            await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(2);
            await expect(page.getByRole("alert")).toHaveCount(0);
        });
        test("changed actor cannot hydrate previous actor response", async ({ page }) => {
            await page.goto(`${path}?scenario=actor`);
            await page.getByRole("button", { name: "Open panel", exact: true }).click();
            await expect(page.locator("[role='status']")).toBeVisible();
            await page.getByRole("button", { name: "Switch actor", exact: true }).click();
            await expect(page.locator("li").filter({ hasText: /\/automation$/ })).toHaveCount(2);
            await page.getByRole("button", { name: "Release", exact: true }).click();
            await page.getByRole("button", { name: "Inspect models" }).click();
            await expect(page.locator("output")).toHaveText("Unhydrated; other: retained");
        });
        test("revoked actor cannot hydrate late result", async ({ page }) => {
            await page.goto(`${path}?scenario=revoke`);
            await page.getByRole("button", { name: "Open panel", exact: true }).click();
            await expect(page.locator("[role='status']")).toBeVisible();
            await page.getByRole("button", { name: "Revoke", exact: true }).click();
            await page.getByRole("button", { name: "Release", exact: true }).click();
            await expect(page.getByRole("alert")).toBeVisible();
            await page.getByRole("button", { name: "Inspect models" }).click();
            await expect(page.locator("output")).toHaveText("Unhydrated; other: retained");
        });
    });
}
