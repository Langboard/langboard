import { test, expect } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/card/mirror/mirror.fixture.html";
test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("lang", "en-US"));
});
test("authorized source renders read only and opens exact source route", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => {
        errors.push(error.message);
        console.log(error.message);
    });
    await page.goto(fixture);
    await expect(page.getByRole("heading", { name: "Authorized source title" })).toBeVisible();
    await expect(page.getByText("Authorized description", { exact: true })).toBeVisible();
    await expect(page.locator("[data-dashboard-card]").getByRole("button", { name: "Authorized source title" })).toBeVisible();
    await expect(page.locator("[contenteditable=true], textarea, input, [role=checkbox]")).toHaveCount(0);
    await expect(page.getByText("UNTRUSTED LOCAL TITLE")).toHaveCount(0);
    await page.getByRole("button", { name: "Open original" }).click();
    await expect(page.locator("[data-location]")).toHaveText("/board/source-board/source-card");
    await expect(page.locator("[data-request]")).toBeEmpty();
    expect(errors).toEqual([]);
});
for (const [mode, width] of ["forbidden", "missing"].flatMap((mode) => [1440, 768, 390].map((width) => [mode, width] as const))) {
    test(`${mode} at ${width}px suppresses stale source fields and keeps recovery reachable`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`${fixture}?mode=${mode}`);
        await expect(page.getByRole("heading", { name: "Source unavailable" })).toBeVisible();
        await expect(page.getByText("Reference only; not active work", { exact: true })).toBeVisible();
        await expect(page.getByText(/Original content is unavailable in your current access context/)).toBeVisible();
        await expect(page.getByText(/Authorized|UNTRUSTED/)).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Open original" })).toBeDisabled();
        await expect(page.getByRole("button", { name: "Remove from board" })).toBeInViewport();
        await expect(page.locator("[data-card-surface]")).toHaveJSProperty(
            "scrollWidth",
            await page.locator("[data-card-surface]").evaluate((element) => element.clientWidth)
        );
        await expect(page.locator("[contenteditable=true], textarea, input, [role=checkbox]")).toHaveCount(0);
        await page.getByRole("button", { name: "Remove from board" }).click();
        await expect(page.locator("[data-request]")).toHaveText("put /board/destination-board/card/destination-mirror/archive");
        await expect(page.locator("[data-location]")).toHaveText("/board/destination-board");
    });
}
test("authorized removal only addresses destination mirror", async ({ page }) => {
    await page.goto(fixture);
    await page.getByRole("button", { name: "Remove from board" }).click();
    await expect(page.locator("[data-request]")).toHaveText("put /board/destination-board/card/destination-mirror/archive");
});
test("viewer has no removal and invalid source URLs cannot open", async ({ page }) => {
    await page.goto(`${fixture}?mode=viewer`);
    await expect(page.getByRole("button", { name: "Remove from board" })).toHaveCount(0);
    await page.goto(`${fixture}?mode=bad-url`);
    await expect(page.getByRole("button", { name: "Open original" })).toBeDisabled();
});

test("dashboard list opens destination mirror and redacts unavailable title", async ({ page }) => {
    await page.goto(fixture);
    await page.locator("[data-dashboard-card]").getByRole("button", { name: "Authorized source title" }).click();
    await expect(page.locator("[data-location]")).toHaveText("/board/destination-board/destination-mirror");
    await page.goto(`${fixture}?mode=forbidden`);
    await expect(page.locator("[data-dashboard-card]").getByRole("button", { name: "Source unavailable" })).toBeVisible();
});
