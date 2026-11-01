import { test, expect } from "@playwright/test";
const path = "/src/pages/DashboardPage/PersonalSignalInbox.fixture.html";
for (const width of [1920, 390])
    test(`personal source handoff selects existing board workflow ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        await page.goto(path);
        await expect(page.getByText("Second board", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Open source Inbox" }).click();
        await expect(page).toHaveURL(/\/board\/board2\?context=inbox&signal=signal2$/);
        await expect(page.getByRole("combobox", { name: "Card", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: /GitHub · #55/ })).toHaveAttribute("aria-pressed", "true");
        const calls = await page.evaluate(() => (window as unknown as { inboxCalls: { url: string; method: string; params: unknown }[] }).inboxCalls);
        expect(
            calls.some(
                (call) => call.url === "/board/board2/signals/inbox" && JSON.stringify(call.params) === JSON.stringify({ signal_uid: "signal2" })
            )
        ).toBe(true);
        expect(calls.every((call) => call.method === "get")).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
test("bounded pages preserve source identity", async ({ page }) => {
    await page.goto(path + "?pages");
    await expect(page.getByText("First board", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next signals" }).click();
    await expect(page.getByText("Second board", { exact: true })).toBeVisible();
    await expect(page.getByText("First board", { exact: true })).toHaveCount(0);
});
test("account switch aborts stale results and denial clears the view", async ({ page }) => {
    await page.goto(path + "?delayed");
    await page.getByRole("button", { name: "Switch account" }).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("Second board", { exact: true })).toHaveCount(0);
    await page.goto(path + "?denied");
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Open source Inbox" })).toHaveCount(0);
});
test("readonly source handoff cannot expose mutation forms", async ({ page }) => {
    await page.goto(path + "?readonly");
    await page.getByRole("button", { name: "Open source Inbox" }).click();
    await expect(page.getByRole("combobox", { name: "Card", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Link evidence", exact: true })).toHaveCount(0);
});
