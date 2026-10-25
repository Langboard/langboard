import { expect, test } from "@playwright/test";
const fixture = "/src/pages/DashboardPage/TrackingLoad.fixture.html";
for (const mobile of [false, true]) {
    test(`initial failure differs from empty and explicit retry recovers ${mobile ? "mobile" : "desktop"}`, async ({ page }) => {
        if (mobile) await page.setViewportSize({ width: 390, height: 844 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(`${fixture}?mode=initial-error`);
        await expect(page.locator("[data-requests]")).toContainText(/"page":1/);
        await expect(page.getByRole("alert")).toHaveText("Could not load trackingRetry");
        await expect(page.getByText("No checkitems", { exact: true })).toHaveCount(0);
        await expect(page.getByText("PRIVATE RAW ERROR")).toHaveCount(0);
        await page.screenshot({ path: `../../.verification/tracking-retry-wave6-20261026/initial-error-${mobile}.png` });
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.getByRole("button", { name: "Tracked task 1", exact: true })).toBeVisible();
        await expect(page.getByRole("alert")).toHaveCount(0);
        const requests = JSON.parse(await page.locator("[data-requests]").innerText());
        expect(requests.map((request: { page: number }) => request.page)).toEqual([1, 1]);
        expect(requests[0].refer_time).toBe(requests[1].refer_time);
        await page.screenshot({ path: `../../.verification/tracking-retry-wave6-20261026/recovered-${mobile}.png` });
        expect(errors).toEqual([]);
    });
}
test("loading does not flash empty; successful data hydrates and remains reactive", async ({ page }) => {
    await page.goto(`${fixture}?mode=loading`);
    await expect(page.locator("[aria-busy]")).toHaveAttribute("aria-busy", "true");
    await expect(page.getByText("No checkitems", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Tracked task 1", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Update card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Updated card", exact: true })).toHaveCount(3);
    await expect(page.locator("[data-requests]")).toContainText(/"page":1/);
});
test("true empty follows successful response without retry", async ({ page }) => {
    await page.goto(`${fixture}?mode=empty`);
    await expect(page.getByText("No checkitems", { exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
});
test("failed next page retains rows and retries same cursor once without duplicate append", async ({ page }) => {
    await page.goto(`${fixture}?mode=more-error`);
    await expect(page.getByRole("button", { name: "Tracked task 1", exact: true })).toBeVisible();
    await page.locator("#main").evaluate((node) => {
        node.scrollTop = node.scrollHeight;
    });
    await expect(page.getByRole("alert")).toBeVisible();
    await page.locator("#main").evaluate((node) => {
        node.scrollTop = 0;
    });
    await expect(page.getByRole("button", { name: "Tracked task 1", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.locator("#main").evaluate((node) => {
        node.scrollTop = node.scrollHeight;
    });
    await expect(page.getByRole("button", { name: "Tracked task 35", exact: true })).toBeVisible();
    const requests = JSON.parse(await page.locator("[data-requests]").innerText());
    expect(requests.map((request: { page: number }) => request.page)).toEqual([1, 2, 2]);
    expect(new Set(requests.map((request: { refer_time: string }) => request.refer_time)).size).toBe(1);
});

for (const [language, message] of [
    ["ko-KR", "시간 추적 기록을 불러오지 못했습니다"],
    ["ja-JP", "時間追跡の記録を読み込めませんでした"],
    ["zh-CN", "无法加载时间跟踪记录"],
]) {
    test(`tracking error uses native ${language} translation`, async ({ page }) => {
        await page.goto(`${fixture}?mode=initial-error&lang=${language}`);
        await expect(page.getByRole("alert").locator("span")).toHaveText(message);
        await expect(page.getByRole("alert").getByRole("button")).toBeVisible();
    });
}

for (const width of [390, 1280]) {
    test(`long tracking titles stay inside their cells at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(`${fixture}?mode=long-titles`);
        const task = page.getByRole("button", { name: "VeryLongUnbrokenTrackedTaskTitle1", exact: true });
        const card = page.getByRole("button", { name: "VeryLongUnbrokenAuthorizedCardTitle", exact: true }).first();
        await expect(task).toBeVisible();
        for (const link of [task, card]) {
            const bounds = await link.evaluate((node) => {
                const button = node.getBoundingClientRect();
                const cell = node.parentElement!.getBoundingClientRect();
                return {
                    left: button.left,
                    right: button.right,
                    cellLeft: cell.left,
                    cellRight: cell.right,
                    contentWidth: node.scrollWidth,
                    visibleWidth: node.clientWidth,
                };
            });
            expect(bounds.left).toBeGreaterThanOrEqual(bounds.cellLeft);
            expect(bounds.right).toBeLessThanOrEqual(bounds.cellRight);
            expect(bounds.contentWidth).toBeLessThanOrEqual(bounds.visibleWidth);
        }
        await page.screenshot({ path: `../../.verification/wave17-tracking/long-titles-${width}.png` });
        await task.click();
        await expect(page).toHaveURL(/\/board\/tracking-project\/tracking-card$/);
        expect(errors).toEqual([]);
    });
}
