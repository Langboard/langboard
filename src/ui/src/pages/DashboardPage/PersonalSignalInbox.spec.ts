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
            { expected_generation: 1, event_kinds: ["test", topic, ...(initiallyEnabled ? ["missed_summary"] : [])], project_uids: projectUIDs, ...(mode === "personal" ? { reminder_project_uids: [] } : {}) },
            { expected_generation: 1, event_kinds: ["test", topic, ...(!initiallyEnabled ? ["missed_summary"] : [])], project_uids: projectUIDs, ...(mode === "personal" ? { reminder_project_uids: [] } : {}) },
        ]);
    });
}

type TestState = { recovered: boolean; denied: boolean; revokeConsent: boolean; refreshes: number };
type TestWrite = { subscription_uid: string; request_id: string; expected_generation: number };
const testState = (page: import("@playwright/test").Page, update: Partial<TestState>) =>
    page.evaluate((value) => Object.assign((window as unknown as { inboxTestState: TestState }).inboxTestState, value), update);
const testWrites = (page: import("@playwright/test").Page) =>
    page.evaluate(() => (window as unknown as { inboxTestWrites: TestWrite[] }).inboxTestWrites);
const chooseTest = async (page: import("@playwright/test").Page, query = "") => {
    await page.goto(path + "?testNotification" + query);
    await page.getByRole("combobox", { name: "App", exact: true }).selectOption("example");
    await page.getByRole("combobox", { name: "Personal connection" }).selectOption("connection");
    await page.getByRole("combobox", { name: "Destination", exact: true }).selectOption("room");
    await page.getByRole("combobox", { name: "Subscription", exact: true }).selectOption("subscription");
};
const fillTest = async (page: import("@playwright/test").Page) => {
    await page.getByRole("textbox", { name: "Test title (120 characters maximum)" }).fill("Synthetic title");
    await page.getByRole("textbox", { name: "Test body (2000 characters maximum)" }).fill("Synthetic body");
    await page.getByRole("checkbox", { name: /I confirm this personal destination/ }).check();
};
const sendTest = async (page: import("@playwright/test").Page) => {
    await fillTest(page);
    await page.getByRole("button", { name: "Send TEST notification", exact: true }).click();
};
for (const width of [1920, 390]) {
    test(`personal TEST composer uses explicit confirmation, caps and keyboard labels ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        await chooseTest(page);
        const send = page.getByRole("button", { name: "Send TEST notification", exact: true });
        await expect(send).toBeDisabled();
        await expect(page.getByRole("textbox", { name: /Test title/ })).toHaveAttribute("maxlength", "120");
        await expect(page.getByRole("textbox", { name: /Test body/ })).toHaveAttribute("maxlength", "2000");
        await fillTest(page);
        await page.getByRole("textbox", { name: /Test title/ }).fill("Edited synthetic title");
        await expect(send).toBeDisabled();
        await page.getByRole("checkbox", { name: /I confirm this personal destination/ }).check();
        await send.focus();
        await page.keyboard.press("Enter");
        await expect(page.getByRole("status")).toHaveText("Original TEST result: Pending");
        expect(await testWrites(page)).toHaveLength(1);
        expect(await page.evaluate(() => (window as unknown as { inboxTestState: TestState }).inboxTestState.refreshes)).toBe(1);
        await expect(page.getByRole("textbox", { name: /Test title/ })).toHaveValue("");
        await expect(page.getByRole("textbox", { name: /Test body/ })).toHaveValue("");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}
test("double activation issues one guarded TEST POST and one matching GET", async ({ page }) => {
    await chooseTest(page, "&slowTest");
    await fillTest(page);
    await page.getByRole("button", { name: "Send TEST notification", exact: true }).evaluate((button) => {
        (button as HTMLButtonElement).click();
        (button as HTMLButtonElement).click();
    });
    await expect(page.getByRole("status")).toHaveText("Original TEST result: Pending");
    const writes = await testWrites(page);
    expect(writes).toHaveLength(1);
    const lookups = await page.evaluate(() =>
        (window as unknown as { inboxCalls: { url: string; method: string; params: unknown }[] }).inboxCalls.filter(
            (call) => call.url === "/settings/apps/notification-outbox"
        )
    );
    expect(lookups).toEqual([
        {
            url: "/settings/apps/notification-outbox",
            method: "get",
            params: {
                subscription_uid: writes[0].subscription_uid,
                request_id: writes[0].request_id,
            },
        },
    ]);
});
test("lost POST response recovers exact tuple without another send", async ({ page }) => {
    await chooseTest(page, "&lostPost");
    await sendTest(page);
    await expect(page.getByRole("status")).toHaveText("Original TEST result: Pending");
    expect(await testWrites(page)).toHaveLength(1);
    await expect(page.getByText("Confirmed", { exact: true })).toHaveCount(0);
});
test("empty lookup keeps original identity unresolved until explicit GET recovery", async ({ page }) => {
    await chooseTest(page, "&lostPost&emptyLookup");
    await sendTest(page);
    const check = page.getByRole("button", { name: "Check test result" });
    await expect(check).toBeEnabled();
    await expect(page.getByRole("status")).toContainText("Test result unresolved");
    await expect(page.getByRole("button", { name: "Send TEST notification", exact: true })).toHaveCount(0);
    const original = await testWrites(page);
    await check.click();
    await expect(check).toBeEnabled();
    expect(await testWrites(page)).toEqual(original);
    await testState(page, { recovered: true });
    await check.click();
    await expect(page.getByRole("status")).toHaveText("Original TEST result: Pending");
    const requests = await page.evaluate(() =>
        (window as unknown as { inboxCalls: { url: string; params: unknown }[] }).inboxCalls
            .filter((call) => call.url === "/settings/apps/notification-outbox")
            .map((call) => call.params)
    );
    expect(requests).toEqual(Array(3).fill({ subscription_uid: original[0].subscription_uid, request_id: original[0].request_id }));
    expect(await testWrites(page)).toHaveLength(1);
});
for (const query of ["oldLookup&lostPost", "mismatchLookup", "differentEvent"]) {
    test(`ordinary or mismatched lookup cannot resolve original TEST: ${query}`, async ({ page }) => {
        await chooseTest(page, "&" + query);
        await sendTest(page);
        await expect(page.getByRole("button", { name: "Check test result" })).toBeEnabled();
        await expect(page.getByRole("status")).toContainText("Test result unresolved");
        await expect(page.getByRole("button", { name: "Send TEST notification", exact: true })).toHaveCount(0);
        expect(await testWrites(page)).toHaveLength(1);
        expect(await page.evaluate(() => (window as unknown as { inboxTestState: TestState }).inboxTestState.refreshes)).toBe(0);
    });
}
test("original neutral lookup survives selection and authority revision changes", async ({ page }) => {
    await chooseTest(page, "&lostPost&emptyLookup");
    await sendTest(page);
    await expect(page.getByRole("button", { name: "Check test result" })).toBeEnabled();
    const original = await testWrites(page);
    await page.getByRole("combobox", { name: "Subscription", exact: true }).selectOption("");
    await expect(page.getByRole("textbox", { name: /Test title/ })).toHaveCount(0);
    await testState(page, { revokeConsent: true });
    await page.getByRole("button", { name: "Refresh settings", exact: true }).click();
    await testState(page, { recovered: true });
    await page.getByRole("button", { name: "Check test result" }).click();
    await expect(page.getByRole("status")).toHaveText("Original TEST result: Pending");
    expect(await testWrites(page)).toEqual(original);
});
test("hidden in-flight TEST clears sensitive content, fences late result and retains explicit lookup", async ({ page }) => {
    await chooseTest(page, "&slowTest");
    await sendTest(page);
    await expect.poll(async () => (await testWrites(page)).length).toBe(1);
    await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(550);
    await expect(page.getByRole("region", { name: "Personal TEST notification" })).toHaveCount(0);
    await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.getByRole("textbox", { name: /Test title/ })).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("Test result unresolved");
    await page.getByRole("button", { name: "Check test result" }).click();
    await expect(page.getByRole("status")).toHaveText("Original TEST result: Pending");
    expect(await testWrites(page)).toHaveLength(1);
});
test("account switch fences in-flight TEST and discards old actor state", async ({ page }) => {
    await chooseTest(page, "&slowTest");
    await sendTest(page);
    await expect.poll(async () => (await testWrites(page)).length).toBe(1);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await page.waitForTimeout(550);
    await expect(page.getByText("Account switched", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Personal TEST notification" })).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { inboxTestState: TestState }).inboxTestState.refreshes)).toBe(0);
});
test("denied reconciliation clears title body and receipt; no automatic resend", async ({ page }) => {
    await chooseTest(page, "&deniedTest");
    await sendTest(page);
    await expect(page.getByRole("alert")).toContainText("Unable to verify this test");
    await expect(page.getByRole("textbox", { name: /Test title/ })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: /Test body/ })).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("Test result unresolved");
    expect(await testWrites(page)).toHaveLength(1);
});
test("current test consent is required and stale consent cannot send", async ({ page }) => {
    await chooseTest(page, "&noConsent");
    await expect(page.getByRole("region", { name: "Personal TEST notification" })).toHaveCount(0);
    expect(await testWrites(page)).toHaveLength(0);
    await chooseTest(page);
    await fillTest(page);
    await testState(page, { revokeConsent: true });
    await page.getByRole("button", { name: "Send TEST notification", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Unable to verify this test");
    expect(await testWrites(page)).toHaveLength(0);
    await expect(page.getByRole("textbox", { name: /Test title/ })).toHaveValue("");
});
test("shared mode exposes no personal TEST composer", async ({ page }) => {
    await page.goto(path + "?summary=shared");
    await page.getByRole("combobox", { name: "Destination scope" }).selectOption("shared");
    await page.getByRole("combobox", { name: "App", exact: true }).selectOption("example");
    await page.getByRole("combobox", { name: "Personal connection" }).selectOption("connection");
    await page.getByRole("combobox", { name: "Destination", exact: true }).selectOption("room");
    await page.getByRole("combobox", { name: "Subscription", exact: true }).selectOption("subscription");
    await expect(page.getByRole("region", { name: "Personal TEST notification" })).toHaveCount(0);
    expect(await testWrites(page)).toHaveLength(0);
});

for (const [status, label] of [
    ["transport_delivered", "Transport delivered"],
    ["adapter_accepted", "Adapter accepted"],
]) {
    test(`TEST acceptance renders existing status without claiming delivery: ${status}`, async ({ page }) => {
        await chooseTest(page, "&testStatus=" + status);
        await sendTest(page);
        await expect(page.getByRole("status")).toHaveText("Original TEST result: " + label);
        expect(await testWrites(page)).toHaveLength(1);
        await expect(page.getByText("Confirmed", { exact: true })).toHaveCount(0);
    });
}
