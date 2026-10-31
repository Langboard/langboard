import { expect, test } from "@playwright/test";

const fixture = "/src/controllers/api/board/useGetCards.fixture.html";
test("slow and failed enrichment cannot block board data; refresh recovers only current-card metadata", async ({ page }) => {
    await page.goto(fixture);
    await expect(page.getByRole("heading", { name: "Board ready" })).toBeVisible();
    await expect(page.getByText("Authorized card", { exact: true })).toBeVisible();
    await expect(page.getByText("Metadata entries: 0")).toBeVisible();
    await expect(page.locator("li")).toHaveCount(2);
    await page.getByRole("button", { name: "Reject metadata" }).click();
    await expect(page.getByRole("heading", { name: "Board ready" })).toBeVisible();
    await page.getByRole("button", { name: "Refresh board" }).click();
    await expect(page.getByText("Metadata entries: 1")).toBeVisible();
    await expect(page.locator("li")).toHaveCount(4);
});
test("a denied primary snapshot never loads enrichment or displays a card", async ({ page }) => {
    await page.goto(fixture + "?deny=1");
    await expect(page.getByRole("heading", { name: "Board denied" })).toBeVisible();
    await expect(page.getByText("Authorized card", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Metadata entries: 0")).toBeVisible();
    await expect(page.locator("li")).toHaveCount(1);
});

test("a new observer after failure enriches only its fresh snapshot", async ({ page }) => {
    await page.goto(fixture);
    await expect(page.locator("li")).toHaveCount(2);
    await page.getByRole("button", { name: "Reject metadata" }).click();
    await page.getByRole("button", { name: "Enable second observer" }).click();
    await expect(page.locator("li")).toHaveCount(4);
    await page.getByRole("button", { name: "Release metadata" }).click();
    await expect(page.getByText("Metadata value: loaded-2")).toBeVisible();
});
test("a replaced snapshot aborts late enrichment instead of overwriting fresh metadata", async ({ page }) => {
    await page.goto(fixture);
    await expect(page.locator("li")).toHaveCount(2);
    await page.getByRole("button", { name: "Refresh board" }).click();
    await expect(page.getByText("Metadata value: loaded-2")).toBeVisible();
    await expect(page.getByText("Aborted metadata: 1")).toBeVisible();
    await page.getByRole("button", { name: "Release metadata" }).click();
    await expect(page.getByText("Metadata value: loaded-2")).toBeVisible();
    await expect(page.locator("li")).toHaveCount(4);
});

test("assigned outstanding work follows the snapshot actor and native assignment updates", async ({ page }) => {
    await page.goto(fixture);
    const summary = page.locator("[data-board-work-summary]");
    await expect(summary).toContainText("Assigned outstanding work: 1");
    await page.getByRole("button", { name: "Assign second actor", exact: true }).click();
    await expect(summary).toContainText("Assigned outstanding work: 0");
    await page.getByRole("button", { name: "Assign current actor by socket" }).click();
    await expect(summary).toContainText("Assigned outstanding work: 1");
    await page.getByRole("button", { name: "Inject cache card" }).click();
    await expect(page.getByText("Realtime cache card", { exact: true })).toBeVisible();
    await expect(summary).toContainText("1/1");
    await expect(summary).toContainText("Assigned outstanding work: 1");
    await page.getByRole("button", { name: "Swap actor", exact: true }).click();
    await expect(summary).not.toContainText("Assigned outstanding work");
    await page.getByRole("button", { name: "Release board", exact: true }).click();
    await expect(summary).toContainText("Assigned outstanding work: 0");
    await page.getByRole("button", { name: "Assign second actor", exact: true }).click();
    await expect(summary).toContainText("Assigned outstanding work: 1");
});

for (const boundary of [
    "Complete authoritative state",
    "Set archived work",
    "Set reference work",
    "Set unknown lifecycle work",
    "Set unknown material work",
    "Set unknown completion work",
    "Clear work state",
]) {
    test(`${boundary} excludes assigned work from the loaded snapshot count`, async ({ page }) => {
        await page.goto(fixture);
        const summary = page.locator("[data-board-work-summary]");
        await expect(summary).toContainText("Assigned outstanding work: 1");
        await page.getByRole("button", { name: boundary, exact: true }).click();
        await expect(summary).toContainText("Assigned outstanding work: 0");
        await expect(summary).toContainText("1/1");
    });
}

for (const swap of ["Swap session", "Swap board", "Invalidate snapshot", "Fail refresh"]) {
    test(`${swap} invalidates the assigned count`, async ({ page }) => {
        await page.goto(fixture);
        const summary = page.locator("[data-board-work-summary]");
        await expect(summary).toContainText("Assigned outstanding work: 1");
        await page.getByRole("button", { name: swap, exact: true }).click();
        await expect(summary).not.toContainText("Assigned outstanding work");
    });
}

for (const boundary of ["Set null readiness work", "Set policy excluded work", "Set blocked work"]) {
    test(`${boundary} retains assigned outstanding backlog without claiming execution readiness`, async ({ page }) => {
        await page.goto(fixture);
        const summary = page.locator("[data-board-work-summary]");
        await expect(summary).toContainText("Assigned outstanding work: 1");
        await page.getByRole("button", { name: boundary, exact: true }).click();
        await expect(summary).toContainText("Assigned outstanding work: 1");
        if (boundary === "Set blocked work") await expect(summary).toContainText("Blocked: 1");
    });
}
