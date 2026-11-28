import { expect, test } from "@playwright/test";
test("authenticated bootstrap loads notifications once through the header", async ({ page }) => {
    await page.goto("/src/core/providers/NotificationLoad.fixture.html");
    await expect(page.locator("#state")).toHaveText("loaded");
    await expect(page.locator("#reads")).toHaveText("1");
});

for (const surfaceName of ["header", "center"] as const) {
    test(`${surfaceName} keeps the shell usable when the inbox response is malformed`, async ({ page }) => {
        const errors: Error[] = [];
        page.on("pageerror", (error) => errors.push(error));
        await page.goto(
            `/src/core/providers/NotificationLoad.fixture.html?${surfaceName === "header" ? "inbox&malformed=page" : "center&malformed=thread"}`
        );
        await expect(page.locator("#state")).toHaveText("loaded");
        const surface = surfaceName === "header" ? page.locator("[data-notification-surface=content]") : page.getByRole("main");
        if (surfaceName === "header") await page.getByRole("button", { name: "Notifications", exact: true }).click();
        const error = surface.getByRole("alert");
        await expect(error).toBeVisible();
        await expect(page.locator("#reads")).toHaveText("1");
        await error.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(surface.getByText("Grouped fixture card", { exact: true })).toBeVisible();
        await expect(error).toHaveCount(0);
        await expect(page.locator("#reads")).toHaveText("2");
        expect(errors).toEqual([]);
    });
}

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
    await expect(surface.getByRole("button", { name: "Snooze" })).toHaveCount(0);
    await surface.getByRole("button", { name: "Read notification", exact: true }).click();
    await expect(page.locator("#observed-read")).toHaveText("event000001,event000002");
    await expect(surface.getByText("Grouped fixture card")).toHaveCount(0);
    await expect(surface.getByRole("button", { name: "Go to notification center" })).toBeVisible();
});

test("notification popup snoozes only the reminder in a mixed card row", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 879 });
    await page.goto("/src/core/providers/NotificationLoad.fixture.html?inbox&reminder");
    await page.getByRole("button", { name: "Notifications", exact: true }).click();
    const surface = page.locator("[data-notification-surface=content]");
    await surface.getByRole("button", { name: "Snooze", exact: true }).click();
    await expect(page.getByRole("menuitem", { name: "1 hour", exact: true })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "24 hours", exact: true })).toBeVisible();
    await page.getByRole("menuitem", { name: "10 minutes", exact: true }).click();
    await expect(page.locator("#snoozed")).toHaveText("/notifications/inbox/events/event000001/snooze:10");
    await expect(page.locator("#observed-read")).toBeEmpty();
    await expect(surface.getByText("Grouped fixture card")).toBeVisible();
    await expect(surface.getByText("1 unread", { exact: true })).toBeVisible();
    await expect(surface.getByRole("button", { name: "Snooze", exact: true })).toHaveCount(0);
});

for (const width of [390, 1457]) {
    test(`notification center fits triage controls and keeps observed selection at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 879 });
        await page.goto("/src/core/providers/NotificationLoad.fixture.html?center");
        const center = page.getByRole("main");
        const row = center.getByRole("listitem").filter({ hasText: "Grouped fixture card" });
        await expect(row.getByText("Unread mention")).toBeVisible();
        await expect(row.getByRole("button", { name: "Mark as done", exact: true })).toBeVisible();
        await expect.poll(() => center.evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);

        await expect(center.getByRole("heading", { name: "Fixture board", exact: true })).toBeVisible();
        await expect(center.getByRole("heading", { name: "Second board", exact: true })).toBeVisible();
        await expect(row.getByText("Priority P0", { exact: true })).toBeVisible();
        await expect(row.getByText("Fixture Actor", { exact: true })).toBeVisible();
        await expect(row.getByText("Review", { exact: true })).toBeVisible();
        await expect(row.getByText(/Subscription reasons.*Card creator.*Mentioned/)).toBeVisible();
        await center.getByRole("button", { name: "Load more", exact: true }).click();
        const boardHeading = center.getByRole("heading", { name: "Fixture board", exact: true });
        await expect(boardHeading).toHaveCount(1);
        const earlier = boardHeading.locator("../..").getByRole("checkbox", { name: "Earlier card on first board", exact: true });
        await expect(earlier).toBeVisible();
        const select = center.getByRole("checkbox", { name: "Select displayed in Fixture board", exact: true });
        await select.check();
        await expect(row.getByRole("checkbox")).toBeChecked();
        await expect(earlier).toBeChecked();
        await expect(center.getByRole("checkbox", { name: "Other board card", exact: true })).not.toBeChecked();
        await page.getByRole("button", { name: "Add fixture activity" }).click();
        await expect(row.getByText("3 unread")).toBeVisible();
        await expect(select).toBeChecked();
        await center.getByRole("combobox", { name: "Group notifications", exact: true }).selectOption("none");
        await expect(center.getByRole("heading", { name: "Second board", exact: true })).toHaveCount(0);
        await expect(row.getByRole("checkbox")).toBeChecked();
        const bulk = center.locator('[aria-label="Bulk actions"]');
        await bulk.getByRole("button", { name: "Read notification", exact: true }).click();
        await expect(page.locator("#observed-read")).toHaveText("event000001,event000002,event000005");
    });
}

test("keyboard can cancel and reopen the snooze menu without closing notifications", async ({ page }) => {
    await page.goto("/src/core/providers/NotificationLoad.fixture.html?inbox&reminder");
    await page.getByRole("button", { name: "Notifications", exact: true }).press("Enter");
    const surface = page.locator("[data-notification-surface=content]");
    const snooze = surface.getByRole("button", { name: "Snooze", exact: true });
    await snooze.press("Enter");
    await expect(page.getByRole("menuitem", { name: "10 minutes" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(snooze).toBeFocused();
    await expect(page.locator("#snoozed")).toBeEmpty();
    await page.keyboard.press("Enter");
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "1 hour" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#snoozed")).toHaveText("/notifications/inbox/events/event000001/snooze:60");
    await expect(surface.getByText("Grouped fixture card")).toBeVisible();
    await expect(page.locator("#observed-read")).toBeEmpty();
});
