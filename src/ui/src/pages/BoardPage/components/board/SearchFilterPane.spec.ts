import { test, expect } from "@playwright/test";
test("board pane filters follow their own router and preserve independent history", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/SearchFilterPane.fixture.html?filters=columns:global");
    const left = page.getByRole("region", { name: "Left pane" });
    const right = page.getByRole("region", { name: "Right pane" });
    await expect(left.locator("output")).toHaveText(JSON.stringify({ columns: ["left"] }));
    await expect(right.locator("output")).toHaveText(JSON.stringify({ columns: ["right"] }));
    await right.getByRole("button", { name: "Change filter", exact: true }).click();
    await expect(right.locator("output")).toHaveText(JSON.stringify({ columns: ["next"], keyword: ["hello world"] }));
    await expect(left.locator("output")).toHaveText(JSON.stringify({ columns: ["left"] }));
    await left.getByRole("button", { name: "Refresh filter", exact: true }).click();
    await expect(right.locator("output")).toHaveText(JSON.stringify({ columns: ["next"], keyword: ["hello world"] }));
    await right.getByRole("button", { name: "Back", exact: true }).click();
    await expect(right.locator("output")).toHaveText(JSON.stringify({ columns: ["right"] }));
    await expect(left.locator("output")).toHaveText(JSON.stringify({ columns: ["left"] }));
    await expect(page).toHaveURL(/filters=columns:global$/);
});

test("pane compose replaces its own history entry and global destinations leave the scope", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/SearchFilterPane.fixture.html");
    const left = page.getByRole("region", { name: "Left pane" });
    const right = page.getByRole("region", { name: "Right pane" });
    await right.getByRole("button", { name: "Change filter", exact: true }).click();
    await right.getByRole("button", { name: "Compose", exact: true }).click();
    await expect(right.locator("[data-pane-location]")).toContainText("new-card=1");
    await expect(left.locator("[data-pane-location]")).not.toContainText("new-card");
    await expect(right.locator("output")).toHaveText(JSON.stringify({ columns: ["next"], keyword: ["hello world"] }));
    await right.getByRole("button", { name: "Back", exact: true }).click();
    await expect(right.locator("output")).toHaveText(JSON.stringify({ columns: ["right"] }));
    await right.getByRole("button", { name: "Forward", exact: true }).click();
    await expect(right.locator("[data-pane-location]")).toContainText("new-card=1");
    await expect(page.locator("[data-outer-location]")).toHaveText("/workspace");
    await right.getByRole("button", { name: "Dashboard", exact: true }).click();
    await expect(page.locator("[data-outer-location]")).toHaveText("/dashboard/projects/all");
    await expect(left.locator("output")).toHaveText(JSON.stringify({ columns: ["left"] }));
});
