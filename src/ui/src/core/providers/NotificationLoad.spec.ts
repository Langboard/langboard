import { expect, test } from "@playwright/test";
test("authenticated bootstrap loads notifications once through the header", async ({ page }) => {
    await page.goto("/src/core/providers/NotificationLoad.fixture.html");
    await expect(page.locator("#state")).toHaveText("loaded");
    await expect(page.locator("#reads")).toHaveText("1");
});

for (const width of [390, 1457]) {
    test(`notification header keeps its title on one line at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 879 });
        await page.goto("/src/core/providers/NotificationLoad.fixture.html?lang=ko");
        await expect(page.locator("#state")).toHaveText("loaded");
        await page.getByRole("button", { name: "알림", exact: true }).click();
        const surface = page.locator("[data-notification-surface=content]");
        const title = surface.getByText("알림", { exact: true });
        await expect(title).toBeVisible();
        const geometry = await title.evaluate((el) => {
            const box = el.getBoundingClientRect();
            return { height: box.height, lineHeight: Number.parseFloat(getComputedStyle(el).lineHeight) };
        });
        expect(geometry.height).toBeLessThanOrEqual(geometry.lineHeight + 1);
        const bounds = await surface.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        await expect(surface.getByRole("switch")).toBeVisible();
        await surface.screenshot({ path: `../../.verification/ui-feedback-20261020/notifications-${width}.png` });
    });
}

test("card activity never presents a default while its preference is unresolved", async ({ page }) => {
    await page.goto("/src/core/providers/NotificationLoad.fixture.html?activity&lang=ko");
    const activity = page.locator("#activity");
    await expect(activity.getByRole("status")).toHaveText("로딩 중...");
    await expect(activity.getByRole("slider")).toHaveCount(0);

    await activity.getByRole("button", { name: "Resolve activity read" }).click();
    const error = activity.getByRole("alert");
    await expect(error).toBeVisible();
    await expect(activity.getByRole("status")).toHaveCount(0);
    await expect(activity.getByRole("slider")).toHaveCount(0);
    await error.getByRole("button").click();
    await expect(activity.getByRole("slider")).toHaveAttribute("aria-valuetext", "댓글 + 카드 이동");
});

test("notification popup displays one card row and marks its observed events together", async ({ page }) => {
    page.on("pageerror", (error) => {
        throw error;
    });
    await page.goto("/src/core/providers/NotificationLoad.fixture.html?inbox");
    await expect(page.locator("#reads")).toHaveText("1");
    const trigger = page.getByRole("button", { name: "Notifications", exact: true });
    await expect(trigger).toHaveText("1");
    await trigger.click();
    const surface = page.locator("[data-notification-surface=content]");
    await expect(surface.getByText("Grouped fixture card")).toHaveCount(1);
    await expect(surface.getByText("Unread mention")).toBeVisible();
    await expect(surface.getByText("2 unread")).toBeVisible();
    await surface.getByRole("button", { name: "Read notification", exact: true }).click();
    await expect(page.locator("#observed-read")).toHaveText("event000001,event000002");
    await expect(surface.getByText("Grouped fixture card")).toHaveCount(0);
    await expect(surface.getByRole("button", { name: "Go to notification center" })).toBeVisible();
});
