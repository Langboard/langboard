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
