import { expect, test } from "@playwright/test";

test("card detail hydrates items once and preserves notifications, edges, timer and refresh replacement", async ({ page }) => {
    await page.goto("/src/controllers/api/card/useGetCardDetails.fixture.html");
    const summary = page.getByTestId("summary");
    await expect(summary).toContainText(JSON.stringify({ reads: 1 }).slice(1, -1));
    const initial = JSON.parse((await summary.textContent())!);
    expect(initial.conversions).toBe(3);
    expect(initial.creations).toBe(3);
    expect(initial.connected).toBe(3);
    expect(initial.lists.map((list: { items: { uid: string }[] }) => list.items.map((item) => item.uid))).toEqual([
        ["second", "first"],
        ["third"],
        [],
    ]);
    expect(initial.lists[0].items[0]).toMatchObject({ worker: "worker", child: "child-card", seconds: 23, started: "2026-10-11T00:00:00.000Z" });
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(summary).toContainText(JSON.stringify({ reads: 2 }).slice(1, -1));
    const refreshed = JSON.parse((await summary.textContent())!);
    expect(refreshed.conversions).toBe(4);
    expect(refreshed.creations).toBe(3);
    expect(refreshed.connected).toBe(3);
    expect(refreshed.lists.map((list: { items: { uid: string }[] }) => list.items.map((item) => item.uid))).toEqual([["first"], [], []]);
    expect(refreshed.lists[0].items[0].title).toBe("Changed");
});

test("partial detail hydration retains omitted body, attachments and checklist edges", async ({ page }) => {
    await page.goto("/src/controllers/api/card/useGetCardDetails.fixture.html?partial=1");
    const summary = page.getByTestId("summary");
    await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(1);
    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(2);
    let value = JSON.parse((await summary.textContent())!);
    expect(value.retainedDescription).toBe("Body:account-a:detail-card");
    expect(value.retainedAttachments).toEqual(["attachment-detail-card"]);
    expect(value.retainedChecklistItems).toEqual(["second", "first"]);
    await page.getByRole("button", { name: "Receipts", exact: true }).click();
    await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(3);
    value = JSON.parse((await summary.textContent())!);
    expect(value.retainedDescription).toBe("Body:account-a:detail-card");
    expect(value.retainedAttachments).toEqual(["attachment-detail-card"]);
    expect(value.retainedChecklistItems).toEqual(["second", "first"]);
    expect(value.calls.map((call: { sections: string }) => call.sections)).toEqual(["checklists", "attachments,description", "execution_receipts"]);
});

for (const destination of ["card", "account"])
    test(`superseded ${destination} responses abort before hydrating shared models`, async ({ page }) => {
        await page.goto("/src/controllers/api/card/useGetCardDetails.fixture.html?partial=1");
        const summary = page.getByTestId("summary");
        await expect(summary).toContainText("account-a:detail-card");
        await page.getByRole("button", { name: "Deferred refresh", exact: true }).click();
        await page.getByRole("button", { name: destination === "card" ? "Switch card" : "Switch account", exact: true }).click();
        await expect(summary).toContainText(destination === "card" ? "account-a:other-card" : "account-b:detail-card");
        const before = JSON.parse((await summary.textContent())!);
        expect(before.calls[1].aborted).toBe(true);
        const counts = [before.conversions, before.creations, before.connected];
        await page.getByRole("button", { name: "Release", exact: true }).click();
        // Read counters directly: cancelled queries must not mutate models without a React render.
        await expect
            .poll(() => page.evaluate(() => (window as unknown as { detailFixtureCounts: () => number[] }).detailFixtureCounts()))
            .toEqual(counts);
        await expect(summary).toContainText(destination === "card" ? "account-a:other-card" : "account-b:detail-card");
    });

for (const mode of ["missing", "malformed"]) {
    test(`action authority uses immutable current response despite ${mode} merged capabilities`, async ({ page }) => {
        await page.goto(`/src/controllers/api/card/useGetCardDetails.fixture.html?authority=${mode}`);
        const summary = page.getByTestId("summary");
        await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(1);
        const initial = JSON.parse((await summary.textContent())!);
        expect(initial.authority).toEqual({ role_actions: ["*"], native_card: true, is_check_card: true, completed: true });
        expect(initial.frozenAuthority).toBe(true);
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(2);
        const refreshed = JSON.parse((await summary.textContent())!);
        expect(refreshed.authority).toEqual({ role_actions: [], native_card: false, is_check_card: false });
        expect(refreshed.frozenAuthority).toBe(true);
        if (mode === "missing") expect(refreshed.modelRoles).toEqual(["*"]);
    });
}

for (const mode of ["missing", "malformed"]) {
    test(`workflow destinations are immutable response facts despite ${mode} flags and shared model grants`, async ({ page }) => {
        await page.goto(`/src/controllers/api/card/useGetCardDetails.fixture.html?workflow=${mode}`);
        const summary = page.getByTestId("summary");
        await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(1);
        let value = JSON.parse((await summary.textContent())!);
        expect(value.workflow).toEqual({ source_column_uid: "column", completed: false, destinations: [{ uid: "done", name: "Done" }] });
        expect(value.frozenWorkflow).toBe(true);
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await expect.poll(async () => JSON.parse((await summary.textContent())!).reads).toBe(2);
        value = JSON.parse((await summary.textContent())!);
        expect(value.workflow.destinations).toEqual([]);
        expect(value.frozenWorkflow).toBe(true);
        if (mode === "missing") expect(value.modelCompletion).toBe(true);
        await page.getByRole("button", { name: "Socket grant", exact: true }).click();
        expect(JSON.parse((await summary.textContent())!).workflow.destinations).toEqual([]);
    });
}
