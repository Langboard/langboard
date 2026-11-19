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

test("mixed providers retain their source names without empty commit separators", async ({ page }) => {
    await page.goto(path + "?providers");
    await expect(page.getByText("second/repo · GitHub · #55 · aaaaaaaaaaaa", { exact: true })).toBeVisible();
    await expect(page.getByText("errors · GlitchTip · #issue7", { exact: true })).toBeVisible();
    await expect(page.getByText("application · Dokploy · #deploy8", { exact: true })).toBeVisible();
    const calls = await page.evaluate(() => (window as unknown as { inboxCalls: { method: string }[] }).inboxCalls);
    expect(calls.every((call) => call.method === "get")).toBe(true);
});

for (const action of ["Turn off subscription", "Remove destination"]) {
    test(`confirmed ${action} clears the obsolete selection`, async ({ page }) => {
        await page.goto(path + "?revoke");
        await page.getByRole("combobox", { name: "Destination scope" }).selectOption("shared");
        await page.getByRole("combobox", { name: "App", exact: true }).selectOption("example");
        await page.getByRole("combobox", { name: "Personal connection" }).selectOption("connection");
        const destination = page.getByRole("combobox", { name: "Destination", exact: true });
        const subscription = page.getByRole("combobox", { name: "Subscription" });
        const deadline = page.getByRole("checkbox", { name: "Receive deadline notices for this board" });
        await destination.selectOption("room");
        await subscription.selectOption("subscription");
        await expect(deadline).toBeChecked();
        await page.getByRole("button", { name: action, exact: true }).click();
        await page.getByRole("button", { name: "Confirm", exact: true }).click();
        if (action === "Turn off subscription") {
            await expect(destination).toHaveValue("room");
            await expect(subscription).toHaveValue("");
            await expect(deadline).not.toBeChecked();
        } else {
            await expect(destination).toHaveValue("");
            await expect(subscription).toHaveCount(0);
        }
        await expect(page.getByRole("button", { name: "Turn off subscription" })).toHaveCount(0);
    });
}

for (const mode of ["personal", "shared"]) {
    test(`recovery summary requires an explicit consent change for ${mode}`, async ({ page }) => {
        await page.goto(path + `?summary=${mode}`);
        await page.getByRole("combobox", { name: "Destination scope" }).selectOption(mode);
        await page.getByRole("combobox", { name: "App", exact: true }).selectOption("example");
        await page.getByRole("combobox", { name: "Personal connection" }).selectOption("connection");
        await page.getByRole("combobox", { name: "Destination", exact: true }).selectOption("room");
        const summary = page.getByRole("checkbox", { name: /Summarize.*for this subscription/ });
        if (mode === "shared") await expect(summary).toHaveCount(0);
        await page.getByRole("combobox", { name: "Subscription" }).selectOption("subscription");
        const initiallyEnabled = mode === "personal";
        await expect(summary).toBeChecked({ checked: initiallyEnabled });
        await summary.setChecked(!initiallyEnabled);
        await page.getByRole("button", { name: "Refresh settings", exact: true }).click();
        await expect(summary).toBeChecked({ checked: initiallyEnabled });

        const save = page.getByRole("button", { name: "Turn on notifications", exact: true });
        await save.click();
        await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
        await summary.setChecked(!initiallyEnabled);
        await save.click();
        await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
        await expect(summary).toBeChecked({ checked: !initiallyEnabled });
        const writes = await page.evaluate(() => (window as unknown as { inboxScopeWrites: unknown[] }).inboxScopeWrites);
        const topic = mode === "personal" ? "card_activity" : "card_deadline";
        const projectUIDs = mode === "personal" ? [] : ["board2"];
        expect(writes).toEqual([
            { expected_generation: 1, event_kinds: ["test", topic, ...(initiallyEnabled ? ["missed_summary"] : [])], project_uids: projectUIDs },
            { expected_generation: 1, event_kinds: ["test", topic, ...(!initiallyEnabled ? ["missed_summary"] : [])], project_uids: projectUIDs },
        ]);
    });
}
