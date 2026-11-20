import { test, expect } from "@playwright/test";
const preview = {
    group_uid: "group-local",
    group_name: "Engineering",
    snapshot: "a".repeat(64),
    target_count: 1,
    member_count: 4,
    counts: { new: 1, existing: 1, inactive: 1, pending: 1 },
    members: {
        new: [{ uid: "new", name: "New member" }],
        existing: [{ uid: "old", name: "Current member" }],
        inactive: [{ uid: "inactive", name: "Inactive" }],
        pending: [{ uid: "pending", name: "Pending" }],
    },
};
for (const stale of [false, true])
    test(`native 320px keyboard confirmation; stale=${stale}`, async ({ page }) => {
        const applies: Record<string, unknown>[] = [];
        let groupReads = 0;
        await page.route("**/settings/groups?**", (route) => {
            groupReads++;
            return route.fulfill({ json: { records: [{ uid: "group-local", display_name: "Engineering" }] } });
        });
        await page.route("**/members/group-preview?**", (route) => route.fulfill({ json: preview }));
        await page.route("**/members/group-add", async (route) => {
            applies.push(route.request().postDataJSON());
            await route.fulfill({ status: stale ? 409 : 200, json: { target_count: 1, changed_count: 1 } });
        });
        await page.goto("/src/pages/BoardPage/components/board/BoardGroupInvitation.fixture.html?lng=en-US");
        await page.getByText("Canonical groups", { exact: true }).focus();
        await page.keyboard.press("Enter");
        await page.getByRole("combobox", { name: "Canonical group", exact: true }).selectOption("group-local");
        await page.getByRole("button", { name: "Preview members" }).focus();
        await page.keyboard.press("Enter");
        const apply = page.getByRole("button", { name: "Invite 1 members" });
        await expect(apply).toBeDisabled();
        await expect(page.getByText("New invitation targets: 1")).toBeVisible();
        for (const label of ["Existing members: 1", "Inactive or deleted: 1", "Invitation pending: 1"])
            await expect(page.getByText(label, { exact: true })).toBeVisible();
        await page.getByRole("checkbox").focus();
        await page.keyboard.press("Space");
        await expect(apply).toBeEnabled();
        await apply.focus();
        await page.keyboard.press("Enter");
        await expect(page.getByRole("status")).toHaveText(
            stale ? "Group, board or permissions changed. Load a fresh preview." : "Group invitation applied. Existing members preserved."
        );
        expect(applies).toHaveLength(1);
        expect(applies[0]).toMatchObject({ confirmed: true, confirmed_count: 1, snapshot: preview.snapshot, group_uid: "group-local" });
        expect(Object.keys(applies[0]).sort()).toEqual(["confirmed", "confirmed_count", "group_uid", "request_id", "snapshot"]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await expect(page.getByRole("checkbox")).toHaveCount(0);
        await page.clock.install();
        const readsBeforeLocaleChange = groupReads;
        for (const locale of ["ko-KR", "ja-JP", "zh-CN", "en-US"]) {
            const expected = await page.evaluate(
                async ({ locale, stale }) => {
                    const modulePath = "/src/i18n.ts";
                    const i18n = (await import(/* @vite-ignore */ modulePath)).default;
                    await i18n.changeLanguage(locale);
                    return i18n.t(stale ? "board.groupInvite.Refresh" : "board.groupInvite.Applied");
                },
                { locale, stale }
            );
            await page.clock.runFor(300);
            await expect(page.getByRole("status")).toHaveText(expected);
            expect(groupReads).toBe(readsBeforeLocaleChange);
            expect(applies).toHaveLength(1);
        }
    });
