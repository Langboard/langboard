import { expect, test } from "@playwright/test";
type FixtureWindow = Window & {
    dashboardUnreadFixture: {
        requests: { count: number; actor?: string; aborted: boolean }[];
        setCount: (count: number) => void;
        delay: () => void;
        release: () => void;
        changeActor: (uid: string) => void;
        reconnect: () => void;
    };
    emitNativeSummaryEvent: (topic: string, uid: string, event: string, data: unknown) => void;
};
for (const width of [320, 1440])
    for (const dark of [false, true]) {
        test(`shared indicator boundaries and live model updates at ${width}, dark=${dark}`, async ({ page }, testInfo) => {
            const errors: string[] = [];
            page.on("pageerror", (e) => errors.push(e.message));
            await page.setViewportSize({ width, height: 1000 });
            await page.goto("/src/pages/DashboardPage/components/dashboard-unread.fixture.html");
            if (dark) await page.locator("html").evaluate((el) => el.classList.add("dark"));
            for (const section of ["Compact", "Cards"]) {
                const area = page.getByRole("region", { name: section, exact: true });
                for (const [index, count, text] of [
                    [0, 0, ""],
                    [1, 1, ""],
                    [2, 2, "2"],
                    [3, 99, "99"],
                    [4, 100, "99+"],
                    [5, 105, "99+"],
                    [6, 1, ""],
                ] as const) {
                    const item = area.locator(`[data-project-uid="unread-${index}"]`);
                    const indicator = item.locator("[data-board-unread-count]");
                    if (!count) {
                        await expect(indicator).toHaveCount(0);
                        continue;
                    }
                    await expect(indicator).toHaveText(text);
                    await expect(indicator).toHaveAttribute("aria-label", index === 6 ? "Unread changes" : `${count} unread board cards`);
                    const badgeBox = await indicator.boundingBox();
                    const starBox = await item.getByRole("button", { name: "Unstar this project", exact: true }).boundingBox();
                    expect(
                        badgeBox &&
                            starBox &&
                            (badgeBox.x + badgeBox.width <= starBox.x ||
                                badgeBox.y + badgeBox.height <= starBox.y ||
                                starBox.y + starBox.height <= badgeBox.y)
                    ).toBeTruthy();
                    expect(await indicator.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe("none");
                }
            }
            await page.getByRole("button", { name: "Apply live count" }).click();
            await expect(page.locator("[data-board-unread-count='105']")).toHaveCount(4);
            await page.getByRole("button", { name: "Mark board read" }).click();
            await expect(page.locator("[data-project-uid='unread-2'] [data-board-unread-count]")).toHaveCount(0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            expect(errors).toEqual([]);
            await page.screenshot({ path: testInfo.outputPath("dashboard-unread.png"), fullPage: true });
        });
    }
for (const [locale, label] of [
    ["ko-KR", "보드 미열람 카드 105개"],
    ["ja-JP", "ボードの未読カード 105 件"],
    ["zh-CN", "105 张看板未读卡片"],
]) {
    test(`accessible count remains actual at ${locale}`, async ({ page }) => {
        await page.goto(`/src/pages/DashboardPage/components/dashboard-unread.fixture.html?lang=${locale}`);
        await expect(page.getByRole("img", { name: label, exact: true })).toHaveCount(2);
        await expect(page.getByRole("img", { name: label, exact: true }).first()).toHaveText("99+");
    });
}

test("native query, socket activity and reconnect refresh shared model indicators", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/src/pages/DashboardPage/components/dashboard-unread.fixture.html?live");
    await expect(page.locator("[data-board-unread-count='2']")).toHaveCount(2);
    await page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.setCount(105));
    await page.evaluate(() =>
        (window as unknown as FixtureWindow).emitNativeSummaryEvent("dashboard", "live-board", "dashboard:project:activity:recorded:live-board", {
            recorded_at: new Date().toISOString(),
        })
    );
    await expect(page.locator("[data-board-unread-count='105']")).toHaveCount(2);
    await page.evaluate(() => {
        (window as unknown as FixtureWindow).dashboardUnreadFixture.setCount(1);
        (window as unknown as FixtureWindow).dashboardUnreadFixture.reconnect();
    });
    await expect(page.locator("[data-board-unread-count='1']")).toHaveCount(2);
    expect(errors).toEqual([]);
});

test("native board read cancels an older dashboard snapshot before refreshing board cursor", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/src/pages/DashboardPage/components/dashboard-unread.fixture.html?live");
    await expect(page.locator("[data-board-unread-count='2']")).toHaveCount(2);
    await page.waitForTimeout(400);
    const before = await page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.requests.length);
    await page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.delay());
    await expect.poll(() => page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.requests.length)).toBe(before + 1);
    await page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.setCount(0));
    await page.getByRole("button", { name: "Read native board", exact: true }).click();
    await expect(page.locator("[data-board-unread-count]")).toHaveCount(0);
    await page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.release());
    await expect
        .poll(() => page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.requests.some((r) => r.aborted)))
        .toBe(true);
    await expect(page.locator("[data-board-unread-count]")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as FixtureWindow).dashboardUnreadFixture.requests.length)).toBe(before + 2);
    expect(errors).toEqual([]);
});
