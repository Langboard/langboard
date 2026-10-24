import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

async function pullLeft(page: Page, { x = 12, y = 120, dx = 130, dy = 0 } = {}) {
    const box = (await page.getByTestId("pan").boundingBox())!;
    await page.mouse.move(box.x + x, box.y + y);
    await page.mouse.down();
    await page.mouse.move(box.x + x + dx, box.y + y + dy, { steps: 8 });
    await page.mouse.up();
}

test("left boundary pull reuses first-column composer, focus and glow", async ({ page }) => {
    let posts = 0;
    await page.route("**/board/fixture/**", (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        posts++;
        return route.fulfill({ json: { card: { uid: "unexpected-submit" } } });
    });
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?add-card=1");
    await expect(page.getByPlaceholder("Enter a title")).toHaveCount(0);
    await pullLeft(page);
    const title = page.getByPlaceholder("Enter a title");
    await expect(title).toBeFocused();
    await expect.poll(() => title.evaluate((e) => e.getAnimations().some((a) => a.effect?.getTiming().duration === 700))).toBe(true);
    expect(new URL(page.url()).searchParams.has("new-card")).toBe(false);
    await title.fill("Existing first-column draft");
    await pullLeft(page);
    await expect(title).toBeFocused();
    await expect(title).toHaveValue("Existing first-column draft");
    expect(posts).toBe(0);
});

test("short, vertical, central and protected drags do not compose; background still pans", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?add-card=1");
    await pullLeft(page, { dx: 50 });
    await pullLeft(page, { dy: 60 });
    await pullLeft(page, { x: 180 });
    await pullLeft(page, { y: 10 });
    await expect(page.getByPlaceholder("Enter a title")).toHaveCount(0);
    await pullLeft(page, { x: 220, dx: -150 });
    await expect.poll(() => page.getByTestId("pan").evaluate((e) => e.scrollLeft)).toBeGreaterThan(100);
    await pullLeft(page);
    await expect(page.getByPlaceholder("Enter a title")).toHaveCount(0);
});

for (const mode of ["denied", "mobile", "touch"])
    test(`left pull does not create for ${mode}`, async ({ page }) => {
        if (mode === "mobile") await page.setViewportSize({ width: 390, height: 844 });
        await page.goto(`/src/pages/BoardPage/components/board/BoardFilter.fixture.html?add-card=1${mode === "denied" ? "&pull-denied=1" : ""}`);
        if (mode === "touch") {
            const pan = page.getByTestId("pan");
            await pan.dispatchEvent("pointerdown", { pointerId: 2, pointerType: "touch", isPrimary: true, button: 0, clientX: 12, clientY: 120 });
            await pan.dispatchEvent("pointerup", { pointerId: 2, pointerType: "touch", isPrimary: true, clientX: 142, clientY: 120 });
        } else await pullLeft(page);
        await expect(page.getByPlaceholder("Enter a title")).toHaveCount(0);
    });

test("cancelled pull stays closed and reduced-motion pull focuses without glow", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?add-card=1");
    const box = (await page.getByTestId("pan").boundingBox())!;
    await page.mouse.move(box.x + 12, box.y + 120);
    await page.mouse.down();
    await page.mouse.move(box.x + 150, box.y + 120);
    await page.keyboard.press("Escape");
    await page.mouse.up();
    await expect(page.getByPlaceholder("Enter a title")).toHaveCount(0);
    await pullLeft(page);
    const title = page.getByPlaceholder("Enter a title");
    await expect(title).toBeFocused();
    expect(await title.evaluate((e) => e.getAnimations().length)).toBe(0);
});
test.beforeEach(async ({ page }) => {
    await page.route("**/board/*/filter-preferences", (route) => route.fulfill({ json: { revision: 0, pinned_filters: [] } }));
});

for (const width of [1280, 390])
    test(`favorite filter toggles, persists and removes independently at ${width}px`, async ({ page }) => {
        let stored = { revision: 0, pinned_filters: [] as { key: string; value: string }[] };
        let writes = 0;
        await page.route("**/board/fixture/filter-preferences", async (route) => {
            if (route.request().method() === "PUT") {
                const body = route.request().postDataJSON();
                expect(body.expected_revision).toBe(stored.revision);
                stored = {
                    revision: stored.revision + 1,
                    pinned_filters: body.pinned_filters.map(({ key, value }: { key: string; value: string }) => ({ value, key })),
                };
                writes++;
            }
            await route.fulfill({ json: stored });
        });
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html");
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("button", { name: "Status", exact: true }).click();
        const condition = dialog.getByRole("checkbox", { name: "ready", exact: true });
        await dialog.getByRole("button", { name: "Add favorite: Status: ready", exact: true }).click();
        await expect(condition).not.toBeChecked();
        await expect(dialog.getByRole("button", { name: "Remove favorite: Status: ready", exact: true })).toHaveAttribute("aria-pressed", "true");
        await page.keyboard.press("Escape");
        await expect(dialog).not.toBeVisible();
        const pin = page.getByRole("button", { name: "Toggle favorite filter: Status: ready", exact: true });
        await expect(pin).toHaveAttribute("aria-pressed", "false");
        await pin.click();
        await expect(pin).toHaveAttribute("aria-pressed", "true");
        await expect(page.getByRole("region", { name: "Results" })).not.toContainText("Active mine");
        await pin.focus();
        await expect(pin).toBeFocused();
        await page.keyboard.press("Space");
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Active mine");
        await pin.click();
        await page.getByRole("button", { name: "Clear", exact: true }).click();
        await expect(pin).toHaveAttribute("aria-pressed", "false");
        expect(writes).toBe(1);
        await page.goto(`/src/pages/BoardPage/components/board/BoardFilter.fixture.html${new URL(page.url()).search}`);
        await expect(pin).toHaveAttribute("aria-pressed", "false");
        await pin.click();
        await page.screenshot({ path: `/private/tmp/langboard-deadline-filter-favorites-${width}.png` });
        await page.getByRole("button", { name: "Remove favorite: Status: ready", exact: true }).click();
        await expect(pin).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Remove filter: Status: ready", exact: true })).toBeVisible();
        expect(writes).toBe(2);
        await page.goto(`/src/pages/BoardPage/components/board/BoardFilter.fixture.html${new URL(page.url()).search}`);
        await expect(pin).toHaveCount(0);
        await expect(page.getByRole("region", { name: "Results" })).not.toContainText("Active mine");
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });

test("favorite filter write conflict requires readback and explicit retry", async ({ page }) => {
    let writes = 0;
    await page.route("**/board/fixture/filter-preferences", async (route) => {
        if (route.request().method() === "PUT") {
            writes++;
            await route.fulfill({ status: 409, json: { code: "EX1003" } });
        } else await route.fulfill({ json: { revision: 2, pinned_filters: [{ key: "columns", value: "deleted" }] } });
    });
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html");
    const unavailable = page.getByRole("button", { name: "Toggle favorite filter: Unavailable saved filter", exact: true });
    await expect(unavailable).toBeDisabled();
    const remove = page.getByRole("button", { name: "Remove favorite: Unavailable saved filter", exact: true });
    await remove.click();
    await expect(page.getByRole("button", { name: "Retry favorite filters", exact: true })).toBeVisible();
    await expect(remove).toBeDisabled();
    expect(writes).toBe(1);
    await page.getByRole("button", { name: "Retry favorite filters", exact: true }).click();
    await expect(remove).toBeEnabled();
    expect(writes).toBe(1);
    await expect(unavailable).toBeDisabled();
});

test("late favorite write stays with the original account and board", async ({ page }) => {
    let account = "me";
    const saved = new Map<string, { revision: number; pinned_filters: { key: string; value: string }[] }>();
    const reads: string[] = [];
    let release: (() => void) | undefined;
    let committed = false;
    await page.route("**/board/*/filter-preferences", async (route) => {
        const board = new URL(route.request().url()).pathname.split("/").at(-2);
        const scope = `${account}/${board}`;
        if (route.request().method() === "PUT") {
            const body = route.request().postDataJSON();
            await new Promise<void>((resolve) => {
                release = resolve;
            });
            saved.set(scope, { revision: body.expected_revision + 1, pinned_filters: body.pinned_filters });
            committed = true;
        } else reads.push(scope);
        await route.fulfill({ json: saved.get(scope) ?? { revision: 0, pinned_filters: [] } });
    });
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Status", exact: true }).click();
    await page.getByRole("button", { name: "Add favorite: Status: ready", exact: true }).click();
    await expect.poll(() => !!release).toBe(true);
    await page.keyboard.press("Escape");
    account = "other";
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("preference-scope")).toHaveText("other:fixture");
    await expect.poll(() => reads.includes("other/fixture")).toBe(true);
    release!();
    await expect.poll(() => committed).toBe(true);
    const chip = page.getByRole("button", { name: "Toggle favorite filter: Status: ready", exact: true });
    await expect(chip).toHaveCount(0);
    await page.getByRole("button", { name: "Switch board", exact: true }).click();
    await expect.poll(() => reads.includes("other/otherboard")).toBe(true);
    await expect(chip).toHaveCount(0);
    account = "me";
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect.poll(() => reads.includes("me/otherboard")).toBe(true);
    await expect(chip).toHaveCount(0);
    await page.getByRole("button", { name: "Switch board", exact: true }).click();
    await expect(chip).toHaveAttribute("aria-pressed", "false");
});
for (const width of [1280, 390])
    test(`registry stage filters preserve OR, column AND and URL at ${width}px`, async ({ page }) => {
        let requests = 0;
        await page.route("**/auth/token/refresh", (route) => route.fulfill({ json: { access_token: "synthetic-fixture-token" } }));
        await page.route("**/board/fixture/workflow-stages", async (route) => {
            requests++;
            await route.fulfill({
                json: {
                    stages: [
                        { key: "custom_ready", name: "Custom queue", color: "#123456", is_active: true, translations: {} },
                        { key: "custom_active", name: "Custom execution", color: "#654321", is_active: true, translations: {} },
                    ],
                },
            });
        });
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html");
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Active mine");
        expect(requests).toBe(0);
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("button", { name: "Workflow stage", exact: true }).click();
        await dialog.getByRole("checkbox", { name: "Custom queue", exact: true }).click();
        await expect(dialog.getByRole("checkbox", { name: "Custom queue", exact: true })).toBeChecked();
        await expect(page.getByRole("region", { name: "Results" })).not.toContainText("Active mine");
        await dialog.getByRole("checkbox", { name: "Custom execution", exact: true }).click();
        await expect(dialog.getByRole("checkbox", { name: "Custom execution", exact: true })).toBeChecked();
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Active mine");
        if (width === 390) await dialog.getByRole("button", { name: "Back to categories" }).click();
        await dialog.getByRole("button", { name: "Status", exact: true }).click();
        await dialog.getByRole("checkbox", { name: "ready", exact: true }).click();
        await expect(dialog.getByRole("checkbox", { name: "ready", exact: true })).toBeChecked();
        await expect(page.getByRole("region", { name: "Results" })).not.toContainText("Active mine");
        await page.keyboard.press("Escape");
        await expect(page.getByTestId("url")).toContainText("workflow_stages");
        await page.goto(`/src/pages/BoardPage/components/board/BoardFilter.fixture.html${new URL(page.url()).search}`);
        await expect(page.getByRole("region", { name: "Results" })).not.toContainText("Active mine");
        await page.getByRole("button", { name: "Remove filter: Workflow stage: Custom queue", exact: true }).click();
        await expect(page.getByRole("region", { name: "Results" })).toHaveText("");
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });
for (const width of [1280, 390])
    test(`category filters retain OR/AND and chips at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html");
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await expect(dialog.getByRole("checkbox")).toHaveCount(0);
        await dialog.getByRole("button", { name: "Status", exact: true }).click();
        await dialog.getByRole("checkbox", { name: "ready", exact: true }).click();
        await expect(dialog.getByRole("checkbox", { name: "ready", exact: true })).toBeChecked();
        await dialog.getByRole("checkbox", { name: "active", exact: true }).click();
        await expect(dialog.getByRole("checkbox", { name: "active", exact: true })).toBeChecked();
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Ready unassigned");
        if (width === 390) await dialog.getByRole("button", { name: "Back to categories" }).click();
        await dialog.getByRole("button", { name: "Assignee", exact: true }).click();
        await dialog.getByRole("checkbox", { name: /Assigned to me/ }).click();
        await expect(dialog.getByRole("checkbox", { name: /Assigned to me/ })).toBeChecked();
        await expect(page.getByRole("region", { name: "Results" })).not.toContainText("Ready unassigned");
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Active mine");
        if (width === 390) await dialog.getByRole("button", { name: "Back to categories" }).click();
        await dialog.getByRole("button", { name: "Status", exact: true }).click();
        await expect(dialog.getByRole("checkbox", { name: "ready", exact: true })).toBeChecked();
        await expect(dialog.getByRole("checkbox", { name: "active", exact: true })).toBeChecked();
        await dialog.getByRole("textbox", { name: "Keyword" }).fill("Ready");
        await expect(page.getByRole("region", { name: "Results" })).toHaveText("Ready mine");
        await page.keyboard.press("Escape");
        await expect(dialog).not.toBeVisible();
        await page.getByRole("button", { name: "Remove filter: Keyword: Ready", exact: true }).click();
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Active mine");
        await page.getByRole("button", { name: "Remove filter: Status: active", exact: true }).click();
        await expect(page.getByRole("region", { name: "Results" })).toHaveText("Ready mine");
        await expect(page.getByTestId("url")).toContainText("columns");
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        await dialog.getByRole("button", { name: "Creator", exact: true }).click();
        await dialog.getByRole("checkbox", { name: /Created by me/ }).click();
        await expect(dialog.getByRole("checkbox", { name: /Created by me/ })).toBeChecked();
        await dialog.getByRole("checkbox", { name: /Former User/ }).click();
        await expect(dialog.getByRole("checkbox", { name: /Former User/ })).toBeChecked();
        await page.keyboard.press("Escape");
        await expect(dialog).not.toBeVisible();
        await page.getByRole("button", { name: "Remove filter: Assignee: Assigned to me", exact: true }).click();
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Ready unassigned");
        await page.getByRole("button", { name: "Remove filter: Creator: Created by me", exact: true }).click();
        await expect(page.getByRole("region", { name: "Results" })).toHaveText("Ready unassigned");
        await page.getByRole("button", { name: "Clear", exact: true }).click();
        await expect(page.getByRole("region", { name: "Results" })).toContainText("Ready unassigned");
        await expect(page.getByTestId("url")).toHaveText("");
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });

test("parent and child selections match either across reload", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?filters=parents%3Ab%2Cchildren%3Ab");
    const results = page.getByRole("region", { name: "Results" });
    await expect(results).toContainText("Ready mine");
    await expect(results).toContainText("Active mine");
    await expect(results).toContainText("Ready unassigned");
    await page.reload();
    await expect(results).toContainText("Ready mine");
    await expect(results).toContainText("Ready unassigned");
    await page.getByRole("button", { name: "Remove filter: Parents of: Active mine", exact: true }).click();
    await expect(results).not.toContainText("Ready mine");
    await expect(results).toContainText("Active mine");
    await expect(results).toContainText("Ready unassigned");
});

const deadlineFixtureURL = "/src/pages/BoardPage/components/board/BoardFilter.fixture.html?deadline-fixture=1";
const temporalTitles = [
    "Today future",
    "Today elapsed",
    "Week Monday",
    "Month start",
    "Quarter start",
    "Half start",
    "Year end",
    "Next year",
    "No deadline",
    "Completed overdue",
    "Reference overdue",
    "Suppressed overdue",
    "Archived overdue",
];
const elapsedToday = ["Today future", "Today elapsed", "Completed overdue", "Reference overdue", "Suppressed overdue", "Archived overdue"];
const presetResults: [string, string, string[]][] = [
    ["today", "Today", elapsedToday],
    ["week", "This week", [...elapsedToday, "Week Monday"]],
    ["month", "This month", [...elapsedToday, "Week Monday", "Month start"]],
    ["quarter", "This quarter", [...elapsedToday, "Week Monday", "Month start", "Quarter start"]],
    ["half-year", "This half-year", [...elapsedToday, "Week Monday", "Month start", "Quarter start", "Half start"]],
    ["year", "This year", [...elapsedToday, "Week Monday", "Month start", "Quarter start", "Half start", "Year end"]],
    ["overdue", "Overdue", ["Today elapsed", "Week Monday", "Month start", "Quarter start", "Half start"]],
    ["none", "No deadline", ["No deadline"]],
];
for (const [preset, label, expected] of presetResults)
    test(`deadline preset ${preset} includes and excludes actual cards`, async ({ page }) => {
        await page.clock.setFixedTime(new Date("2026-05-14T12:00:00"));
        await page.goto(deadlineFixtureURL);
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("button", { name: "Deadline", exact: true }).click();
        await dialog.getByRole("checkbox", { name: label, exact: true }).click();
        const results = page.getByRole("region", { name: "Results", exact: true });
        await expect(results.locator("p")).toHaveCount(expected.length);
        for (const title of temporalTitles) await expect(results.getByText(title, { exact: true })).toHaveCount(Number(expected.includes(title)));
        await page.keyboard.press("Escape");
        await page.goto(`${deadlineFixtureURL}&${new URL(page.url()).searchParams.toString()}`);
        await page.reload();
        await expect(results.locator("p")).toHaveCount(expected.length);
        await expect(page.getByTestId("url")).toContainText(`deadlines%3A${preset}`);
    });

test("deadline OR, assignee AND, favorites, clear and live deadline change preserve membership", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-05-14T12:00:00"));
    let stored = { revision: 0, pinned_filters: [] as { key: string; value: string }[] };
    await page.route("**/board/fixture/filter-preferences", async (route) => {
        if (route.request().method() === "PUT") {
            const body = route.request().postDataJSON();
            expect(body.expected_revision).toBe(stored.revision);
            stored = { revision: stored.revision + 1, pinned_filters: body.pinned_filters };
        }
        await route.fulfill({ json: stored });
    });
    await page.goto(deadlineFixtureURL);
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Deadline", exact: true }).click();
    await dialog.getByRole("checkbox", { name: "Today", exact: true }).click();
    await dialog.getByRole("checkbox", { name: "No deadline", exact: true }).click();
    const results = page.getByRole("region", { name: "Results", exact: true });
    await expect(results.locator("p")).toHaveCount(7);
    await dialog.getByRole("button", { name: "Add favorite: Deadline: Today", exact: true }).click();
    await expect.poll(() => stored.revision).toBe(1);
    expect(stored.pinned_filters).toEqual([{ key: "deadlines", value: "today" }]);
    await dialog.getByRole("button", { name: "Assignee", exact: true }).click();
    await dialog.getByRole("checkbox", { name: /Assigned to me/ }).click();
    await expect(results.locator("p")).toHaveText(["Today future"]);
    await page.keyboard.press("Escape");
    await page.goto(`${deadlineFixtureURL}&${new URL(page.url()).searchParams.toString()}`);
    await page.reload();
    await expect(results.locator("p")).toHaveText(["Today future"]);
    await expect(page.getByRole("button", { name: "Toggle favorite filter: Deadline: Today", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Move deadline to next year", exact: true }).click();
    await expect(results.locator("p")).toHaveCount(0);
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await expect(results.locator("p")).toHaveCount(13);
});

test("deadline pane URL histories and clearing remain independent", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-05-14T12:00:00"));
    await page.goto(`${deadlineFixtureURL}&deadline-panes=1`);
    const left = page.getByRole("region", { name: "Left deadline pane", exact: true });
    const right = page.getByRole("region", { name: "Right deadline pane", exact: true });
    await expect(left.getByRole("region", { name: "Results" }).locator("p")).toHaveCount(6);
    await expect(right.getByRole("region", { name: "Results" }).locator("p")).toHaveText(["No deadline"]);
    await right.getByRole("button", { name: "Clear", exact: true }).click();
    await expect(right.getByRole("region", { name: "Results" }).locator("p")).toHaveCount(13);
    await expect(left.getByTestId("url")).toContainText("deadlines:today");
    await expect(left.getByRole("region", { name: "Results" }).locator("p")).toHaveCount(6);
    await left.getByRole("button", { name: "Filters", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Deadline", exact: true }).click();
    await dialog.getByRole("checkbox", { name: "This year", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(left.getByRole("region", { name: "Results" }).locator("p")).toHaveCount(11);
    await expect(right.getByTestId("url")).not.toContainText("deadlines");
    await expect(page).toHaveURL(/deadline-panes=1$/);
});

test("deadline unknown URL and malformed favorite receipts fail closed", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-05-14T12:00:00"));
    await page.route("**/board/fixture/filter-preferences", (route) =>
        route.fulfill({ json: { revision: 1, pinned_filters: [{ key: "deadlines", value: "tomorrow" }] } })
    );
    await page.goto(`${deadlineFixtureURL}&filters=deadlines:tomorrow`);
    await expect(page.getByRole("region", { name: "Results" }).locator("p")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry favorite filters", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Toggle favorite filter:/ })).toHaveCount(0);
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await expect(page.getByRole("region", { name: "Results" }).locator("p")).toHaveCount(13);
});

for (const width of [1280, 390])
    test(`named saved views replace the complete combination, persist and preserve pins at ${width}px`, async ({ page }) => {
        type Pin = { key: string; value: string };
        let account = "me";
        let stored = { revision: 1, pinned_filters: [{ key: "columns", value: "ready" }], saved_views: [] as { name: string; filters: Pin[] }[] };
        await page.route("**/board/fixture/filter-preferences", async (route) => {
            if (account !== "me") return route.fulfill({ json: { revision: 0, pinned_filters: [], saved_views: [] } });
            if (route.request().method() === "PUT") {
                const body = route.request().postDataJSON();
                expect(body.expected_revision).toBe(stored.revision);
                stored = { ...stored, ...body, revision: stored.revision + 1 };
            }
            await route.fulfill({ json: { revision: stored.revision, pinned_filters: stored.pinned_filters, saved_views: stored.saved_views } });
        });
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?filters=members:me,deadlines:week");
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("textbox", { name: "View name", exact: true }).fill("My week");
        await dialog.getByRole("button", { name: "Save view", exact: true }).click();
        await expect(dialog.getByRole("button", { name: "Restore view: My week", exact: true })).toBeEnabled();
        expect(stored.saved_views).toEqual([
            {
                name: "My week",
                filters: [
                    { key: "members", value: "me" },
                    { key: "deadlines", value: "week" },
                ],
            },
        ]);
        expect(stored.pinned_filters).toEqual([{ key: "columns", value: "ready" }]);
        await page.screenshot({ path: `/tmp/ad15-saved-views-${width}.png` });
        await expect(dialog.getByRole("button", { name: "Save view", exact: true })).toBeDisabled();
        await dialog.getByRole("button", { name: "Clear", exact: true }).click();
        await dialog.getByRole("textbox", { name: "Keyword", exact: true }).fill("changed");
        await dialog.getByRole("button", { name: "Restore view: My week", exact: true }).click();
        await expect(page.getByTestId("url")).toHaveText("?filters=members%3Ame%2Cdeadlines%3Aweek");
        // BoardProvider navigates to the production path; remount the actual-component document with that URL query.
        await page.goto(`/src/pages/BoardPage/components/board/BoardFilter.fixture.html${new URL(page.url()).search}`);
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        await dialog.getByRole("button", { name: "Clear", exact: true }).click();
        await dialog.getByRole("button", { name: "Restore view: My week", exact: true }).click();
        await expect(page.getByTestId("url")).toHaveText("?filters=members%3Ame%2Cdeadlines%3Aweek");
        await page.keyboard.press("Escape");
        account = "other";
        await page.getByRole("button", { name: "Switch account", exact: true }).click();
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        await expect(dialog.getByRole("button", { name: "Restore view: My week", exact: true })).toHaveCount(0);
        await page.keyboard.press("Escape");
        account = "me";
        await page.getByRole("button", { name: "Switch account", exact: true }).click();
        await page.getByRole("button", { name: "Switch board", exact: true }).click();
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        await expect(dialog.getByRole("button", { name: "Restore view: My week", exact: true })).toHaveCount(0);
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Switch board", exact: true }).click();
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        await dialog.getByRole("button", { name: "Delete view: My week", exact: true }).click();
        await expect(dialog.getByRole("button", { name: "Restore view: My week", exact: true })).toHaveCount(0);
        expect(stored.pinned_filters).toEqual([{ key: "columns", value: "ready" }]);
    });

test("unavailable named view is inert and stale delete reloads the newer combination", async ({ page }) => {
    let reads = 0;
    await page.route("**/board/fixture/filter-preferences", async (route) => {
        if (route.request().method() === "PUT") return route.fulfill({ status: 409, json: { code: "EX1003" } });
        reads++;
        return route.fulfill({
            json: {
                revision: reads,
                pinned_filters: [],
                saved_views: [
                    {
                        name: "Removed status",
                        filters: [
                            { key: "columns", value: "deleted" },
                            { key: "members", value: "me" },
                        ],
                    },
                    ...(reads > 1 ? [{ name: "Newer week", filters: [{ key: "deadlines", value: "week" }] }] : []),
                ],
            },
        });
    });
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?filters=members:me");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect(page.getByRole("button", { name: "Restore view: Removed status", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Delete view: Removed status", exact: true }).click();
    await expect(page.getByRole("button", { name: "Restore view: Newer week", exact: true })).toBeEnabled();
    await expect(page.getByTestId("url")).toContainText("members:me");
});
