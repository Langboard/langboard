import { expect, test } from "@playwright/test";

const fixture = "/src/controllers/api/board/useGetCards.fixture.html";

test("authorized membership excludes cache injection while ordinary cards retain realtime additions", async ({ page }) => {
    await page.goto(fixture);
    const summary = page.locator("[data-board-work-summary]");
    await expect(summary).toContainText("1/1");
    await expect(summary).toContainText("Review: 1");
    await page.getByRole("button", { name: "Inject cache card" }).click();
    await expect(page.getByText("Realtime cache card", { exact: true })).toBeVisible();
    await expect(summary).toContainText("1/1");
    await expect(summary).toContainText("Review: 1");
    await expect(summary).toContainText("Blocked: 0");
    await page.getByRole("button", { name: "Edit work state" }).click();
    await expect(summary).toContainText("Review: 0");
    await expect(summary).toContainText("Blocked: 1");
    await page.getByRole("button", { name: "Complete authoritative state" }).click();
    await expect(summary).toContainText("Blocked: 0");
    await page.getByRole("button", { name: "Clear work state" }).click();
    await expect(summary).toContainText("Unknown work state: 1");
    await expect(summary).toContainText("Blocked: 0");
    await expect(summary.locator("time")).toHaveAttribute("datetime", /T/);
    await page.getByRole("button", { name: "Remove snapshot card" }).click();
    await expect(summary).toContainText("0/1");
    await expect(summary).toContainText("Cards not loaded: 1");
});

for (const swap of ["Swap actor", "Swap session", "Swap board", "Invalidate snapshot", "Fail refresh"]) {
    test(`${swap} cannot retain prior authorized summary`, async ({ page }) => {
        await page.goto(fixture);
        const summary = page.locator("[data-board-work-summary]");
        await expect(summary).toContainText("1/1");
        await page.getByRole("button", { name: swap, exact: true }).click();
        await expect(summary).toHaveText("Card counts unavailable");
        await expect(summary).not.toContainText("Review: 1");
    });
}

test("a refreshed response without work_state cannot reuse the cached projection", async ({ page }) => {
    await page.goto(fixture);
    const summary = page.locator("[data-board-work-summary]");
    await expect(summary).toContainText("Review: 1");
    await page.getByRole("button", { name: "Refresh without work state" }).click();
    await expect(summary).toContainText("Review: 0");
    await expect(summary).toContainText("Unknown work state: 1");
});
