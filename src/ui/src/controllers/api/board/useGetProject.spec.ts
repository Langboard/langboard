import { expect, test } from "@playwright/test";
const path = "/src/controllers/api/board/useGetProject.fixture.html";
for (const delayed of ["project", "cards"]) {
    test(`switching boards aborts a pending ${delayed} snapshot without stale enrichment`, async ({ page }) => {
        await page.goto(`${path}?delayed=${delayed}`);
        await expect(page.locator("li").filter({ hasText: delayed === "project" ? /\/board\/project-load-fixture$/ : /\/cards$/ })).toHaveCount(1);
        await page.getByRole("button", { name: "Switch board" }).click();
        await expect(page.getByText("Cards ready", { exact: true })).toBeVisible();
        await expect(page.getByText("Aborted primary requests: 1", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Release previous request" }).click();
        await expect(
            page
                .locator("li")
                .filter({ hasText: delayed === "project" ? /\/board\/project-load-fixture\/(dock|visit)$/ : /\/metadata\/.*project-load-fixture/ })
        ).toHaveCount(0);
        if (delayed === "project") {
            await expect(page.getByText("Loaded project: next-board", { exact: true })).toBeVisible();
        }
    });
}
test("cards load while dock is pending and late dock applies", async ({ page }) => {
    await page.goto(path);
    await expect(page.getByText("Cards ready", { exact: true })).toBeVisible();
    await expect(page.getByText("Dock revision: 0", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Release dock" }).click();
    await expect(page.getByText("Dock revision: 1", { exact: true })).toBeVisible();
});
test("dock failure preserves board and explicit project refresh recovers", async ({ page }) => {
    await page.goto(path);
    await expect(page.getByText("Cards ready", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Reject dock" }).click();
    await expect(page.getByRole("heading", { name: "Project ready" })).toBeVisible();
    await page.getByRole("button", { name: "Refresh project" }).click();
    await expect(page.locator("li").filter({ hasText: /\/dock$/ })).toHaveCount(2);
    await page.getByRole("button", { name: "Release dock" }).click();
    await expect(page.getByText("Dock revision: 1", { exact: true })).toBeVisible();
});
test("denied project never reads dock or cards", async ({ page }) => {
    await page.goto(`${path}?deny`);
    await expect(page.getByRole("heading", { name: "Project denied" })).toBeVisible();
    await expect(page.locator("li")).toHaveCount(1);
    await expect(page.getByText("Cards ready", { exact: true })).toHaveCount(0);
});

test("failed project transport exposes keyboard retry and recovers without waiting for dock", async ({ page }) => {
    await page.goto(`${path}?unavailable`);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByText("Cards ready", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /Retry|다시 시도/ }).press("Enter");
    await expect(page.getByText("Cards ready", { exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.locator("li").filter({ hasText: /\/dock$/ })).toHaveCount(1);
});

for (const scenario of ["assigned", "owner", "removed", "signed-out"]) {
    test(`membership event uses the publisher contract for ${scenario}`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(`/src/controllers/api/board/project-membership.fixture.html?scenario=${scenario}`);
        await page.getByRole("button", { name: "Apply membership event" }).click();
        await expect(page.locator("output")).toHaveText(scenario === "removed" ? "Project removed" : "Project retained");
        expect(errors).toEqual([]);
    });
}

test("explicit visit does not block board and failure never retries or fails its loaded snapshot", async ({ page }) => {
    await page.goto(`${path}?visit=pending`);
    await expect(page.getByText("Cards ready", { exact: true })).toBeVisible();
    await expect(page.locator("li").filter({ hasText: /\/visit$/ })).toHaveCount(1);
    await page.getByRole("button", { name: "Reject visit" }).click();
    await expect(page.getByRole("heading", { name: "Project ready" })).toBeVisible();
    await page.getByRole("button", { name: "Release dock" }).click();
    await expect(page.getByText("Dock revision: 1", { exact: true })).toBeVisible();
    await page.waitForTimeout(5_500);
    await expect(page.locator("li").filter({ hasText: /\/visit$/ })).toHaveCount(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "Refresh project" }).click();
    await expect(page.locator("li").filter({ hasText: /\/visit$/ })).toHaveCount(2);
});

test("a response released after sign-out never records a visit", async ({ page }) => {
    await page.goto(`${path}?delayed=project`);
    await expect(page.locator("li")).toHaveCount(1);
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByRole("button", { name: "Release previous request" }).click();
    await page.waitForTimeout(300);
    await expect(page.locator("li").filter({ hasText: /\/visit$/ })).toHaveCount(0);
});
