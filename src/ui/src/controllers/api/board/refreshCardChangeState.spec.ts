import { expect, test } from "@playwright/test";

const path = "/src/controllers/api/board/refreshCardChangeState.fixture.html";
test("one-card state updates without invalidating the board; legacy servers retain recovery", async ({ page }) => {
    await page.goto(path);
    const updated = page.locator("[data-board-updated]");
    await expect(updated).not.toHaveText("0");
    const before = await updated.textContent();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByText("Count: 1", { exact: true })).toBeVisible();
    await expect(updated).toHaveText(before!);
    await page.getByRole("button", { name: "Legacy response" }).click();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByText("Settled: 2", { exact: true })).toBeVisible();
    await expect(updated).not.toHaveText(before!);
});
test("a newer event cancels an older response without a board reload", async ({ page }) => {
    await page.goto(path);
    await expect(page.locator("[data-board-updated]")).not.toHaveText("0");
    const before = await page.locator("[data-board-updated]").textContent();
    await page.getByRole("button", { name: "Hold next" }).click();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.getByRole("button", { name: "New count" }).click();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByText("Count: 5", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Release held" }).click();
    await expect(page.getByText("Settled: 2", { exact: true })).toBeVisible();
    await expect(page.getByText("Count: 5", { exact: true })).toBeVisible();
    await expect(page.locator("[data-board-updated]")).toHaveText(before!);
});
for (const boundary of ["Change actor", "Seen"]) {
    test(`late state cannot cross ${boundary}`, async ({ page }) => {
        await page.goto(path);
        await page.getByRole("button", { name: "Hold next" }).click();
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await page.getByRole("button", { name: boundary, exact: true }).click();
        await page.getByRole("button", { name: "Release held" }).click();
        await expect(page.getByText("Settled: 1", { exact: true })).toBeVisible();
        await expect(page.getByText("Count: 0", { exact: true })).toBeVisible();
    });
}
