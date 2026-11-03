import { expect, test } from "@playwright/test";

const fixture = "/src/pages/DashboardPage/components/project-discovery-ui.fixture.html";
const baseline = process.env.DISCOVERY_BASELINE === "1";
test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => console.log("Discovery page error:", error.message));
});
test("frozen baseline reproduces split view ownership and default sections during search", async ({ page }, testInfo) => {
    test.skip(!baseline);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${fixture}?baseline=1`);
    const favorites = page.getByRole("region", { name: "Favorites", exact: true });
    await expect(favorites).toBeVisible();
    await page.getByRole("button", { name: "Card view", exact: true }).click();
    await expect(favorites).toHaveAttribute("data-project-view", "compact");
    await page.getByRole("textbox", { name: "Search projects", exact: true }).fill("no-matching-project");
    await expect(page.getByText("No projects found", { exact: true })).toBeVisible();
    await expect(favorites).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("project-discovery-before.png"), fullPage: true });
});
for (const width of [320, 390, 1440]) {
    test(`project discovery uses one view and search pipeline at ${width}`, async ({ page }, testInfo) => {
        test.skip(baseline);
        const errors: string[] = [];
        const fetches: string[] = [];
        page.on("request", (request) => {
            if (["fetch", "xhr"].includes(request.resourceType())) fetches.push(request.url());
        });
        page.on("pageerror", (error) => errors.push(error.message));
        await page.setViewportSize({ width, height: 1000 });
        await page.goto(fixture);
        const search = page.getByRole("textbox", { name: "Search projects", exact: true });
        const favorites = page.getByRole("region", { name: "Favorites", exact: true });
        const all = page.getByRole("region", { name: "All projects", exact: true });
        await expect(favorites).toBeVisible();
        const toolbar = page.getByRole("toolbar", { name: "Project filters", exact: true });
        await expect(toolbar.getByRole("group", { name: "Project view", exact: true })).toBeVisible();
        const centers = await toolbar.locator("button, select").evaluateAll((controls) =>
            controls.map((control) => {
                const box = control.getBoundingClientRect();
                return box.top + box.height / 2;
            })
        );
        expect(Math.max(...centers) - Math.min(...centers)).toBeLessThan(2);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        const sectionsBefore = await page.getByRole("region").evaluateAll((regions) => regions.map((region) => region.getAttribute("aria-label")));
        await favorites.getByRole("button", { name: /Show more/ }).click();
        await expect(favorites.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
        await page.getByRole("button", { name: "Card view", exact: true }).click();
        await expect(page.locator("[data-project-view=compact]")).toHaveCount(0);
        await expect(all).toHaveAttribute("data-project-view", "cards");
        await search.fill("Shared Project");
        await expect(page.locator("[data-project-discovery-mode]")).toHaveAttribute("data-project-discovery-mode", "search");
        await expect(all).toHaveCount(0);
        await expect(page.getByRole("status")).toContainText("11");
        await expect(favorites.locator("[data-project-uid=project-0]:visible")).toBeVisible();
        await expect(favorites.getByText("Related to me", { exact: true }).filter({ visible: true })).toBeVisible();
        await expect(page.getByRole("region", { name: "Related to me", exact: true }).locator("[data-project-uid=project-0]")).toHaveCount(0);
        await page.getByRole("button", { name: "Compact view", exact: true }).click();
        const uniqueResults = page.locator("[data-project-discovery-mode=search] [data-project-uid]");
        await expect(uniqueResults).toHaveCount(11);
        expect(new Set(await uniqueResults.evaluateAll((items) => items.map((item) => item.getAttribute("data-project-uid")))).size).toBe(11);
        await expect(page.locator("[data-project-uid=forbidden]")).toHaveCount(0);
        await page.getByRole("button", { name: "Unread changes", exact: true }).click();
        await expect(uniqueResults).toHaveCount(2);
        await page.getByRole("combobox", { name: "Sort projects" }).selectOption("name");
        await page.getByRole("button", { name: "Clear search", exact: true }).click();
        await expect(search).toBeFocused();
        await expect(page.getByRole("button", { name: "Unread changes", exact: true })).toHaveAttribute("aria-pressed", "true");
        await expect(page.getByRole("combobox", { name: "Sort projects" })).toHaveValue("name");
        await page.getByRole("button", { name: "Reset filters", exact: true }).click();
        expect(await page.getByRole("region").evaluateAll((regions) => regions.map((region) => region.getAttribute("aria-label")))).toEqual(
            sectionsBefore
        );
        await expect(favorites.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
        await search.fill("not-found");
        await expect(page.getByText("No projects found", { exact: true })).toBeVisible();
        await expect(favorites).toHaveCount(0);
        await search.fill("   ");
        await expect(all).toBeVisible();
        await search.fill("Shared");
        await search.fill("nope");
        await page.getByRole("button", { name: "Clear search", exact: true }).click();
        await page.waitForTimeout(400);
        await expect(all).toBeVisible();
        await page.getByRole("button", { name: "Card view", exact: true }).click();
        await page.reload();
        await expect(page.getByRole("button", { name: "Card view", exact: true })).toHaveAttribute("aria-pressed", "true");
        await page.screenshot({ path: testInfo.outputPath(`project-discovery-${width}-cards.png`), fullPage: true });
        expect(errors).toEqual([]);
        expect(fetches).toEqual([]);
    });
}

test("authoritative model, column, permissions, account and storage changes stay current", async ({ page }) => {
    test.skip(baseline);
    await page.goto(fixture);
    const search = page.getByRole("textbox", { name: "Search projects", exact: true });
    await search.fill("Shared");
    await page.getByRole("button", { name: "Unread changes", exact: true }).click();
    await expect(page.locator("[data-project-uid]")).toHaveCount(2);
    await page.getByRole("button", { name: "Mark first read", exact: true }).click();
    await expect(page.locator("[data-project-uid]")).toHaveCount(1);
    await page.getByRole("button", { name: "Reset filters", exact: true }).click();
    await page.getByRole("button", { name: "Unfinished work", exact: true }).click();
    await expect(page.locator("[data-project-uid]")).toHaveCount(2);
    await page.getByRole("button", { name: "Finish related work", exact: true }).click();
    await expect(page.locator("[data-project-uid]")).toHaveCount(1);
    await page.getByRole("button", { name: "Reset filters", exact: true }).click();
    await page.getByRole("button", { name: "Revoke first project", exact: true }).click();
    await expect(page.locator("[data-project-uid=project-0]")).toHaveCount(0);
    await page.getByRole("button", { name: "Change project data", exact: true }).click();
    await search.fill("Renamed");
    await expect(page.getByRole("region", { name: "Favorites", exact: true }).locator("[data-project-uid=project-7]")).toBeVisible();
    await page.getByRole("button", { name: "Card view", exact: true }).click();
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(search).toHaveValue("");
    await expect(page.getByRole("button", { name: "Compact view", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByRole("button", { name: "Card view", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.addInitScript(() => {
        Storage.prototype.getItem = () => {
            throw new DOMException("Storage denied");
        };
        Storage.prototype.setItem = () => {
            throw new DOMException("Storage denied");
        };
    });
    await page.reload();
    await expect(search).toBeVisible();
    await page.getByRole("button", { name: "Card view", exact: true }).click();
    await expect(page.getByRole("button", { name: "Card view", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("existing card list paginates growing snapshots and shrinks without remount keys", async ({ page }, testInfo) => {
    test.skip(baseline);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(fixture);
    const search = page.getByRole("textbox", { name: "Search projects", exact: true });
    await search.fill("Project");
    await page.getByRole("button", { name: "Card view", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("11");
    await page.getByRole("button", { name: "Grow projects", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("33");
    await page.locator("#main").evaluate((element) => {
        element.scrollTop = element.scrollHeight;
    });
    await expect(page.locator("[data-project-uid=extra-9]:visible")).toBeVisible({ timeout: 10000 });
    await page.screenshot({ path: testInfo.outputPath("project-discovery-grown-list.png") });
    await page.locator("#main").evaluate((element) => {
        element.scrollTop = 0;
    });
    await page.getByRole("button", { name: "Shrink projects", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("11");
    await page.getByRole("button", { name: "Clear search", exact: true }).click();
    await expect(page.getByRole("region", { name: "All projects", exact: true })).toBeVisible();
    await expect(page.locator("[data-project-uid^=extra-]")).toHaveCount(0);
});
