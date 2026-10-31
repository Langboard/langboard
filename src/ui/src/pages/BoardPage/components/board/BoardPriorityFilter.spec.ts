import { expect, test } from "@playwright/test";

test("native urgency filter uses the persisted value, combines choices and survives URL reload", async ({ page }) => {
    await page.route("**/board/*/filter-preferences", (route) => route.fulfill({ json: { revision: 0, pinned_filters: [], saved_views: [] } }));
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?priority-fixture=1");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Priority", exact: true }).click();
    await dialog.getByRole("checkbox", { name: "Now!", exact: true }).click();
    await page.keyboard.press("Escape");
    const results = page.getByRole("region", { name: "Results" });
    await expect(results).toHaveText("Ready mine");
    const params = new URL(page.url()).searchParams;
    params.set("priority-fixture", "1");
    await page.goto(`/src/pages/BoardPage/components/board/BoardFilter.fixture.html?${params}`);
    await expect(results).toHaveText("Ready mine");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await dialog
        .getByRole("button", { name: /Priority/, exact: false })
        .first()
        .click();
    await dialog.getByRole("checkbox", { name: "Normal", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(results).toContainText("Ready mine");
    await expect(results).toContainText("Ready unassigned");
    await expect(results).not.toContainText("Active mine");
});
