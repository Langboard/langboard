import { expect, test, type Page } from "@playwright/test";
import type { MyWorkPageResult } from "./MyWorkContext";

const pageErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
    const errors: string[] = [];
    pageErrors.set(page, errors);
    page.on("pageerror", (error) => errors.push(error.stack ?? error.message));
    await page.routeWebSocket("ws://localhost:5392/**", () => {});
});
test.afterEach(({ page }) => {
    expect(pageErrors.get(page), "Uncaught browser errors").toEqual([]);
});
const fixture = "/src/pages/DashboardPage/my-work-context/MyWork.fixture.html";
const panel = (page: Page) => page.locator("[data-my-work-context]");
const workspace = (page: Page) => page.locator("[data-my-work-results]");
const board = (page: Page) => page.getByRole("heading", { name: "Board card for restoration" });
type Fixture = {
    primary: string;
    secondary: string;
    requests: { url: string; params: Record<string, unknown>; aborted: boolean }[];
    deny: (value: boolean) => void;
    delay: (value: boolean) => void;
    release: () => void;
    refresh: () => Promise<void>;
    switchActor: () => void;
    newIdentity: () => void;
    logout: () => void;
    refreshToken: () => Promise<void>;
    cache: () => { key: unknown[]; data?: { pages: MyWorkPageResult[]; pageParams: (string | null)[] }; error?: string }[];
};
const workRequests = (page: Page) =>
    page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.requests.filter((item) => item.url === "/dashboard/work/my"));
const workRows = (page: Page) => page.getByRole("button", { name: /^(Assigned|Foreign project|Today|Overdue|Review) task / });
async function failNextPage(page: Page, status: number) {
    let failed = false;
    await page.route("http://127.0.0.1:5392/dashboard/work/my?**", async (route) => {
        if (!failed && new URL(route.request().url()).searchParams.has("cursor")) {
            failed = true;
            await route.fulfill({ status, json: { error: "Injected next-page failure" } });
        } else await route.continue();
    });
}
async function openPanel(page: Page) {
    await page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "My Work", exact: true }).click();
    if (!(await panel(page).isVisible())) {
        const expand = page.getByRole("button", { name: "Expand", exact: true });
        if (await expand.isVisible()) await expand.click();
    }
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).getByRole("button", { name: "All work 64", exact: true })).toBeVisible();
}
async function start(page: Page) {
    await page.goto(fixture);
    await expect(board(page)).toBeVisible();
}

test("opening stays on board; counts include beyond-page review; clear preserves board node, URL filter and scroll", async ({ page }, testInfo) => {
    await start(page);
    expect(await workRequests(page)).toHaveLength(0);
    await openPanel(page);
    await expect(workspace(page)).toHaveCount(0);
    await expect(board(page)).toBeVisible();
    await expect(panel(page).getByText("50 of 64 work items", { exact: true })).toBeVisible();
    await expect(panel(page).getByRole("button", { name: "Assigned to me 61", exact: true })).toBeVisible();
    await expect(panel(page).getByRole("button", { name: "Review / Check 1", exact: true })).toBeVisible();
    await expect(panel(page).getByRole("button", { name: /^Review task / })).toHaveCount(0);
    const scroll = page.locator("[data-board-pane] [id^=board-scrollport]");
    await scroll.evaluate((node) => {
        node.setAttribute("data-preserved-node", "yes");
        node.scrollLeft = 300;
    });
    const scrollBefore = await scroll.evaluate((node) => node.scrollLeft);
    await panel(page).getByRole("button", { name: "Review / Check 1", exact: true }).click();
    await expect(workspace(page).getByRole("button", { name: /^Review task / })).toBeVisible();
    await expect(workspace(page).getByText("1 of 1 work items", { exact: true })).toBeVisible();
    await expect(board(page)).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath("desktop-review.png") });
    await workspace(page).getByRole("button", { name: "Clear", exact: true }).click();
    await expect(workspace(page)).toHaveCount(0);
    await expect(board(page)).toBeVisible();
    await expect(scroll).toHaveAttribute("data-preserved-node", "yes");
    expect(await scroll.evaluate((node) => node.scrollLeft)).toBe(scrollBefore);
    await expect(page.locator("[data-native-location]")).toContainText("filters=keyword:restoration");
});

test("All scope filters central results across projects; group pagination and native card navigation", async ({ page }) => {
    await start(page);
    await openPanel(page);
    await panel(page).getByRole("button", { name: "All projects", exact: true }).click();
    await expect(workspace(page).getByText("50 of 65 work items", { exact: true })).toBeVisible();
    await expect(workspace(page).getByRole("button", { name: /^Foreign project task / })).toBeVisible();
    await panel(page).getByRole("button", { name: "Assigned to me 62", exact: true }).click();
    await expect(workspace(page).getByText("50 of 62 work items", { exact: true })).toBeVisible();
    await workspace(page).getByRole("button", { name: "Load more work", exact: true }).click();
    await expect(workspace(page).getByText("62 of 62 work items", { exact: true })).toBeVisible();
    await expect(workspace(page).getByRole("button", { name: "Load more work", exact: true })).toHaveCount(0);
    await workspace(page)
        .getByRole("button", { name: /^Foreign project task / })
        .click();
    const secondary = await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.secondary);
    await expect(page.locator("[data-native-location]")).toContainText(`/board/${secondary}/`);
    await expect(page.locator("[data-card-viewer]")).toBeVisible();
    await expect(workspace(page)).toHaveCount(0);
});

test("failed permission refresh suppresses both old rows and counts and retry recovers", async ({ page }) => {
    await start(page);
    await openPanel(page);
    await panel(page).getByRole("button", { name: "Today 1", exact: true }).click();
    await expect(workspace(page).getByRole("button", { name: /^Today task / })).toBeVisible();
    await page.evaluate(() => {
        const api = (window as unknown as { myWorkFixture: Fixture }).myWorkFixture;
        api.deny(true);
        return api.refresh();
    });
    await expect(workspace(page).getByRole("alert")).toBeVisible();
    await expect(workspace(page).getByRole("button", { name: /^Today task / })).toHaveCount(0);
    await expect(panel(page).getByRole("button", { name: "Today 1", exact: true })).toHaveCount(0);
    await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.deny(false));
    await workspace(page).getByRole("button", { name: "Retry", exact: true }).click();
    await expect(workspace(page).getByRole("button", { name: /^Today task / })).toBeVisible();
});

test("transient next-page failure preserves authorized rows and retries the failed cursor", async ({ page }) => {
    await page.goto(`${fixture}?entry=/dashboard`);
    await expect(page.getByText("50 of 65 work items", { exact: true })).toBeVisible();
    await expect(workRows(page)).toHaveCount(50);
    const firstRows = await workRows(page).allTextContents();
    await failNextPage(page, 503);
    await page.getByRole("button", { name: "Load more work", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(workRows(page)).toHaveText(firstRows);
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    const failedRequests = await workRequests(page);
    const cursor = failedRequests.at(-1)!.params.cursor;
    expect(cursor).toEqual(expect.any(String));
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByText("65 of 65 work items", { exact: true })).toBeVisible();
    await expect(workRows(page)).toHaveCount(65);
    await expect(page.getByRole("alert")).toHaveCount(0);
    const retried = await workRequests(page);
    expect(retried).toHaveLength(failedRequests.length + 1);
    expect(retried.at(-1)!.params.cursor).toBe(cursor);
});

for (const status of [403, 404]) {
    test(`denied next-page ${status} retires rows and counts until fresh first-page authorization`, async ({ page }) => {
        await page.goto(`${fixture}?entry=/dashboard`);
        await expect(page.getByText("50 of 65 work items", { exact: true })).toBeVisible();
        await failNextPage(page, status);
        await page.getByRole("button", { name: "Load more work", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(workRows(page)).toHaveCount(0);
        await expect(page.getByRole("navigation", { name: "Work groups", exact: true })).toHaveCount(0);
        const cache = await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.cache());
        expect(cache).toHaveLength(1);
        expect(cache[0].data).toEqual({ pages: [], pageParams: [] });
        const failedRequests = await workRequests(page);
        expect(failedRequests.at(-1)!.params.cursor).toEqual(expect.any(String));
        await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.delay(true));
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect.poll(async () => (await workRequests(page)).length).toBe(failedRequests.length + 1);
        expect((await workRequests(page)).at(-1)!.params.cursor).toBeUndefined();
        await expect(workRows(page)).toHaveCount(0);
        await expect(page.getByRole("navigation", { name: "Work groups", exact: true })).toHaveCount(0);
        await page.evaluate(() => {
            const api = (window as unknown as { myWorkFixture: Fixture }).myWorkFixture;
            api.delay(false);
            api.release();
        });
        await expect(page.getByText("50 of 65 work items", { exact: true })).toBeVisible();
        await expect(workRows(page)).toHaveCount(50);
        await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    });
}

test("denied next-page 401 clears the session and requires a fresh first page after identity restoration", async ({ page }) => {
    await page.goto(`${fixture}?entry=/dashboard`);
    await expect(page.getByText("50 of 65 work items", { exact: true })).toBeVisible();
    await failNextPage(page, 401);
    await page.getByRole("button", { name: "Load more work", exact: true }).click();
    await expect(page).toHaveURL("http://127.0.0.1:4192/auth/signin");
    await expect(workRows(page)).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Work groups", exact: true })).toHaveCount(0);
    const cache = await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.cache());
    expect(cache.every((query) => !query.data)).toBe(true);
    const failedRequests = await workRequests(page);
    expect(failedRequests.at(-1)!.params.cursor).toEqual(expect.any(String));
    await page.evaluate(() => {
        (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.newIdentity();
        window.history.pushState(null, "", "/dashboard");
        window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page.getByText("50 of 65 work items", { exact: true })).toBeVisible();
    await expect(workRows(page)).toHaveCount(50);
    const restored = await workRequests(page);
    expect(restored).toHaveLength(failedRequests.length + 1);
    expect(restored.at(-1)!.params.cursor).toBeUndefined();
});

test("dashboard actor switch and same UID identity reset fence delayed responses; token refresh keeps fresh cache", async ({ page }) => {
    await page.goto(`${fixture}?entry=/dashboard`);
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    const before = (await workRequests(page)).length;
    await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.refreshToken());
    expect(await workRequests(page)).toHaveLength(before);
    await page.evaluate(() => {
        const api = (window as unknown as { myWorkFixture: Fixture }).myWorkFixture;
        api.delay(true);
        void api.refresh();
    });
    await expect.poll(async () => (await workRequests(page)).length).toBe(before + 1);
    await page.evaluate(() => {
        const api = (window as unknown as { myWorkFixture: Fixture }).myWorkFixture;
        api.delay(false);
        api.switchActor();
        api.release();
    });
    await expect(page.getByRole("button", { name: "All work 0", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Assigned task / })).toHaveCount(0);
    await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.newIdentity());
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    expect((await workRequests(page)).some((request) => request.aborted)).toBe(true);
    const beforeReset = (await workRequests(page)).length;
    await page.evaluate(() => {
        const api = (window as unknown as { myWorkFixture: Fixture }).myWorkFixture;
        api.delay(true);
        void api.refresh();
    });
    await expect.poll(async () => (await workRequests(page)).length).toBe(beforeReset + 1);
    await page.evaluate(() => {
        const api = (window as unknown as { myWorkFixture: Fixture }).myWorkFixture;
        api.delay(false);
        api.newIdentity();
        api.release();
    });
    await expect.poll(async () => (await workRequests(page)).length).toBe(beforeReset + 2);
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    expect((await workRequests(page))[beforeReset].aborted).toBe(true);
    const cache = await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.cache());
    expect(cache).toHaveLength(1);
    expect(cache[0].data?.pages[0].total).toBe(65);
});

test("mobile explicit filter closes panel and focuses central results; keyboard clear restores board", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await start(page);
    await openPanel(page);
    await panel(page).getByRole("button", { name: "Today 1", exact: true }).click();
    await expect(panel(page)).toBeHidden();
    await expect(workspace(page).getByRole("button", { name: /^Today task / })).toBeVisible();
    await expect(page.locator("[data-my-work-workspace]")).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("mobile-today.png") });
    await workspace(page).getByRole("button", { name: "Clear", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(workspace(page)).toHaveCount(0);
    await expect(board(page)).toBeVisible();
});

test("selected split pane owns filter and native navigation, other pane remains mounted", async ({ page }) => {
    const config = await page.request.get("http://127.0.0.1:5392/fixture").then((response) => response.json());
    await page.goto(`${fixture}?entry=/board/${config.projects[0]}`);
    await expect(board(page)).toBeVisible();
    const secondary = await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.secondary);
    const transfer = await page.evaluateHandle((uid) => {
        const transfer = new DataTransfer();
        transfer.setData("application/x-langboard-board-reference", uid);
        return transfer;
    }, secondary);
    await page.locator("[data-board-pane]").dispatchEvent("drop", { dataTransfer: transfer });
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
    await expect(board(page)).toHaveCount(2);
    await page
        .locator("[data-board-pane]")
        .last()
        .click({ position: { x: 20, y: 20 } });
    await page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "My Work", exact: true }).click();
    if (!(await panel(page).isVisible())) {
        const expand = page.getByRole("button", { name: "Expand", exact: true });
        if (await expand.isVisible()) await expand.click();
    }
    await expect(panel(page).getByRole("button", { name: "All work 1", exact: true })).toBeVisible();
    await panel(page).getByRole("button", { name: "Assigned to me 1", exact: true }).click();
    const focused = page.locator("[data-board-pane-focused=true]");
    const background = page.locator("[data-board-pane-focused=false]");
    await expect(focused.locator("[data-my-work-results]").getByRole("button", { name: /^Foreign project task / })).toBeVisible();
    await expect(background.getByRole("heading", { name: "Board card for restoration" })).toBeVisible();
    await focused
        .locator("[data-my-work-results]")
        .getByRole("button", { name: /^Foreign project task / })
        .click();
    await expect(focused.locator("[data-card-viewer]")).toBeVisible();
    await expect(background.locator("[data-card-viewer]")).toHaveCount(0);
});

const deadline = "2026-11-03T16:45:00+09:00";
async function injectDeadline(page: Page, value: unknown) {
    await page.route("http://127.0.0.1:5392/dashboard/work/my?**", async (route) => {
        const response = await route.fetch();
        const data: MyWorkPageResult = await response.json();
        data.cards[0].deadline_at = value as string;
        data.cards[1].deadline_at = null;
        await route.fulfill({ response, json: data });
    });
}

test("row deadlines and updated times remain distinct, exact and localized after language changes at mobile and desktop widths", async ({
    page,
}, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("lang", "en-US"));
    await injectDeadline(page, deadline);
    await page.goto(`${fixture}?entry=/dashboard`);
    const row = page.getByRole("button", { name: /^Foreign project task / });
    const noDeadlineRow = page.getByRole("button", { name: /^Overdue task / });
    const originalTitle = await row.locator("span").first().textContent();
    const labels = [
        ["en-US", "Deadline", "No deadline", "Updated"],
        ["ko-KR", "마감일", "마감일 없음", "수정일"],
        ["ja-JP", "期限", "期限なし", "更新日時"],
        ["zh-CN", "截止日期", "无截止日期", "更新时间"],
    ];
    for (const [locale, dueLabel, noneLabel, updatedLabel] of labels) {
        await page.evaluate(async (locale) => {
            const url = "/src/i18n.ts";
            await (await import(/* @vite-ignore */ url)).default.changeLanguage(locale);
        }, locale);
        const expected = await page.evaluate(
            ({ deadline, locale }) => new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(deadline)),
            { deadline, locale }
        );
        const dueTime = row.locator(`time[aria-label="${dueLabel}"]`);
        await expect(dueTime).toHaveAttribute("datetime", deadline);
        await expect(dueTime).toHaveText(expected);
        await expect(row).toContainText(`${dueLabel}:`);
        await expect(noDeadlineRow).toContainText(noneLabel);
        await expect(noDeadlineRow.locator("time")).toHaveCount(1);
        const updatedTime = row.locator(`time[aria-label="${updatedLabel}"]`);
        await expect(updatedTime).toBeVisible();
        expect(await updatedTime.getAttribute("datetime")).not.toBe(deadline);
        for (const width of [390, 1440]) {
            await page.setViewportSize({ width, height: 900 });
            const bounds = await row.boundingBox();
            expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
            expect(await row.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await expect(dueTime).toBeVisible();
        }
    }
    await page.screenshot({ path: testInfo.outputPath("deadline-desktop-zh.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: testInfo.outputPath("deadline-mobile-zh.png") });
    expect(await row.locator("span").first().textContent()).toBe(originalTitle);
    await row.click();
    await expect(page.locator("[data-card-viewer]")).toBeVisible();
});

test("malformed deadline response renders retry state without crashing or exposing invalid rows", async ({ page }) => {
    await injectDeadline(page, "invalid date");
    await page.goto(`${fixture}?entry=/dashboard`);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(workRows(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
});

async function actionTransport(page: Page) {
    const state = {
        deadline: null as string | null,
        check: true,
        allowed: true,
        native: true,
        source: undefined as string | undefined,
        fail: false,
        conflict: false,
        confirmation: false,
        linked: false,
        sourceColumn: "column",
        workflowCompleted: false as unknown,
        columns: undefined as Record<string, unknown>[] | undefined,
        omit: [] as string[],
        denyDetail: false,
        reads: 0,
        holdRead: 0,
        writes: [] as { path: string; body: Record<string, unknown> }[],
        hold: false,
        holdWrite: false,
        release: () => {},
    };
    await page.route("http://127.0.0.1:5392/board/*/card/**", async (route) => {
        const request = route.request();
        const headers = {
            "Access-Control-Allow-Origin": "http://127.0.0.1:4192",
            "Access-Control-Allow-Methods": "GET,PUT,POST,OPTIONS",
            "Access-Control-Allow-Headers": "content-type",
        };
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const path = new URL(request.url()).pathname;
        if (request.method() !== "GET") {
            state.writes.push({ path, body: request.postDataJSON() });
            if (state.hold || state.holdWrite)
                await new Promise<void>((resolve) => {
                    state.release = resolve;
                });
            if (state.conflict) return route.fulfill({ status: 409, headers, json: {} });
            if (state.confirmation && request.postDataJSON().move_descendants === undefined)
                return route.fulfill({ headers, json: { confirmation_required: true, confirmation_revision: "revision-1" } });
            if (state.fail) return route.fulfill({ status: 503, headers, json: {} });
            const body = request.postDataJSON();
            if ("parent_uid" in body) {
                state.sourceColumn = body.parent_uid;
                state.workflowCompleted =
                    state.columns?.some((column) => column.uid === body.parent_uid && column.workflow_counts_as_completed === true) ?? false;
            }
            if ("deadline_at" in body) {
                state.deadline = body.deadline_at || null;
                state.check = !!state.deadline;
            }
            return route.fulfill({ headers, json: { completed: body.completed } });
        }
        state.reads++;
        if (state.hold || state.reads === state.holdRead)
            await new Promise<void>((resolve) => {
                state.release = resolve;
            });
        const parts = path.split("/");
        if (state.denyDetail) return route.fulfill({ status: 403, headers, json: {} });
        return route.fulfill({
            headers,
            json: {
                card: Object.fromEntries(
                    Object.entries({
                        uid: parts[4],
                        project_uid: parts[2],
                        created_at: new Date().toISOString(),
                        updated_at: new Date().toISOString(),
                        title: "Action detail",
                        project_column_uid: state.sourceColumn,
                        project_column_name: "Column",
                        order: 0,
                        member_uids: [],
                        project_members: [],
                        relationships: [],
                        labels: [],
                        current_auth_role_actions: state.allowed ? ["*"] : [],
                        can_cross_board: state.native,
                        source_type: state.source,
                        linked_resource: state.linked ? { status: "available" } : undefined,
                        work_state: { completed: state.workflowCompleted },
                        is_check_card: state.check,
                        completed: false,
                        deadline_at: state.deadline,
                    }).filter(([key]) => !state.omit.includes(key))
                ),
                checklists: [],
                project_columns: state.columns,
            },
        });
    });
    return state;
}
const actionRow = (page: Page) => workRows(page).first().locator("..");
async function openActions(page: Page) {
    await page.goto(`${fixture}?entry=/dashboard`);
    await expect(workRows(page)).toHaveCount(50);
    await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).press("Enter");
    await expect(actionRow(page).getByRole("button", { name: "Add deadline", exact: true })).toBeVisible();
}
for (const width of [1440, 390]) {
    test(`direct deadline add/edit/remove uses native transport and refreshes pages at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const state = await actionTransport(page);
        await openActions(page);
        expect(state.reads).toBe(1);
        expect(await page.locator("button button").count()).toBe(0);
        const location = await page.locator("[data-native-location]").textContent();
        await page.getByRole("button", { name: "Load more work", exact: true }).click();
        await expect(workRows(page)).toHaveCount(65);
        await actionRow(page).getByRole("button", { name: "Add deadline", exact: true }).press("Enter");
        await page.getByRole("button", { name: "Done", exact: true }).click();
        await expect.poll(() => state.writes.length).toBe(1);
        await expect(workRows(page)).toHaveCount(50);
        expect(state.writes[0].path).toContain("/details");
        expect(Object.keys(state.writes[0].body)).toEqual(["deadline_at"]);
        await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
        await actionRow(page).getByRole("button", { name: "Set deadline", exact: true }).click();
        for (const day of (await page.getByRole("gridcell").getByRole("button").all()).reverse()) {
            if (await day.isEnabled()) {
                await day.click();
                break;
            }
        }
        await page.getByRole("button", { name: "Done", exact: true }).click();
        await expect.poll(() => state.writes.length).toBe(2);
        await expect(workRows(page)).toHaveCount(50);
        await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
        await actionRow(page).getByRole("button", { name: "Remove deadline", exact: true }).click();
        await expect.poll(() => state.writes.length).toBe(3);
        expect(state.writes[2].body).toEqual({ deadline_at: "" });
        await expect(workRows(page)).toHaveCount(50);
        await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
        await expect(actionRow(page).getByRole("button", { name: "Complete check card", exact: true })).toHaveCount(0);
        expect(await page.locator("[data-native-location]").textContent()).toBe(location);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("check completion targets selected board/card, fails closed after current permission change, then retries", async ({ page }) => {
    const state = await actionTransport(page);
    await page.route("http://127.0.0.1:5392/dashboard/work/my?**", async (route) => {
        const response = await route.fetch();
        const data: MyWorkPageResult = await response.json();
        if (state.writes.length === 2 && !state.fail) {
            data.total--;
            data.counts.overdue--;
            data.cards = data.cards.slice(1);
        }
        await route.fulfill({ response, json: data });
    });
    await openActions(page);
    const row = actionRow(page);
    state.allowed = false;
    await row.getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect(row.getByRole("alert")).toBeVisible();
    expect(state.writes).toHaveLength(0);
    state.allowed = true;
    await row.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(row.getByRole("button", { name: "Complete check card", exact: true })).toBeEnabled();
    state.fail = true;
    await row.getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect(row.getByRole("alert")).toBeVisible();
    state.fail = false;
    await row.getByRole("button", { name: "Retry", exact: true }).click();
    await row.getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(2);
    await expect(page.getByRole("button", { name: "All work 64", exact: true })).toBeVisible();
    expect(state.writes[1]).toEqual({ path: expect.stringMatching(/\/board\/6KZLKlukzZm\/card\/[^/]+\/completion$/), body: { completed: true } });
});

for (const kind of ["ordinary", "owned", "linked"]) {
    test(`${kind} card cannot gain unsupported completion or ownership authority`, async ({ page }) => {
        const state = await actionTransport(page);
        state.check = kind !== "ordinary";
        state.native = kind !== "owned";
        state.source = kind === "linked" ? "project_card" : undefined;
        await page.goto(`${fixture}?entry=/dashboard`);
        await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
        if (kind === "ordinary") await expect(actionRow(page).getByRole("button", { name: "Add deadline", exact: true })).toBeVisible();
        else await expect(actionRow(page).getByText("Card actions unavailable", { exact: true })).toBeVisible();
        await expect(actionRow(page).getByRole("button", { name: "Complete check card", exact: true })).toHaveCount(0);
        expect(state.writes).toHaveLength(0);
    });
}

test("delayed action authorization cannot write after same-UID session replacement", async ({ page }) => {
    const state = await actionTransport(page);
    await openActions(page);
    state.hold = true;
    await actionRow(page).getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect.poll(() => state.reads).toBe(2);
    await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.newIdentity());
    state.hold = false;
    state.release();
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    expect(state.writes).toHaveLength(0);
});

test("pending authorization suppresses duplicates across collapse", async ({ page }) => {
    const state = await actionTransport(page);
    await openActions(page);
    const row = actionRow(page);
    const toggle = row.getByRole("button", { name: "Card actions", exact: true });
    state.hold = true;
    await row.getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect.poll(() => state.reads).toBe(2);
    await expect(row.getByRole("button", { name: "Complete check card", exact: true })).toBeDisabled();
    await toggle.click();
    await toggle.click();
    await expect(row.getByRole("button", { name: "Complete check card", exact: true })).toBeDisabled();
    expect(state.writes).toHaveLength(0);
    state.hold = false;
    state.release();
    await expect.poll(() => state.writes.length).toBe(1);
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
});

for (const change of ["switchActor", "newIdentity"] as const) {
    test(`stale initial action detail after ${change} cannot expose controls`, async ({ page }) => {
        const state = await actionTransport(page);
        state.hold = true;
        await page.goto(`${fixture}?entry=/dashboard`);
        await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
        await expect.poll(() => state.reads).toBe(1);
        await page.evaluate((change) => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture[change](), change);
        state.hold = false;
        state.release();
        await expect(page.getByRole("button", { name: change === "switchActor" ? "All work 0" : "All work 65", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Complete check card", exact: true })).toHaveCount(0);
        expect(state.writes).toHaveLength(0);
    });
}

for (const capability of ["current_auth_role_actions", "is_check_card", "completed", "can_cross_board"]) {
    test(`omitted fresh ${capability} cannot reuse a previously editable grant`, async ({ page }) => {
        const state = await actionTransport(page);
        await openActions(page);
        state.omit = [capability];
        await actionRow(page).getByRole("button", { name: "Complete check card", exact: true }).click();
        await expect(actionRow(page).getByRole("alert")).toBeVisible();
        expect(state.writes).toHaveLength(0);
    });
}

test("denied detail fetch exposes retry without retaining action controls", async ({ page }) => {
    const state = await actionTransport(page);
    await openActions(page);
    state.denyDetail = true;
    await actionRow(page).getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect(actionRow(page).getByRole("alert")).toBeVisible();
    await expect(actionRow(page).getByRole("button", { name: "Complete check card", exact: true })).toHaveCount(0);
    expect(state.writes).toHaveLength(0);
    state.denyDetail = false;
    await actionRow(page).getByRole("button", { name: "Retry", exact: true }).click();
    await expect(actionRow(page).getByRole("button", { name: "Complete check card", exact: true })).toBeEnabled();
});

test("pending completion write survives collapse without duplicate transport", async ({ page }) => {
    const state = await actionTransport(page);
    await openActions(page);
    state.holdWrite = true;
    const row = actionRow(page);
    await row.getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(1);
    await row.getByRole("button", { name: "Card actions", exact: true }).click();
    await row.getByRole("button", { name: "Card actions", exact: true }).click();
    await expect(row.getByRole("button", { name: /^(Complete|Reopen) check card$/ })).toBeDisabled();
    expect(state.writes).toHaveLength(1);
    state.holdWrite = false;
    state.release();
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
});

test("StrictMode effect replay retains current action ownership", async ({ page }) => {
    const state = await actionTransport(page);
    await page.goto(`${fixture}?strict&entry=/dashboard`);
    await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
    await expect(actionRow(page).getByRole("button", { name: "Complete check card", exact: true })).toBeEnabled();
    await actionRow(page).getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(1);
});

test("rejected deadline removal keeps confirmed deadline and retry rereads capability", async ({ page }) => {
    const state = await actionTransport(page);
    state.deadline = "2027-12-20T09:00:00Z";
    await page.goto(`${fixture}?entry=/dashboard`);
    await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
    state.fail = true;
    await actionRow(page).getByRole("button", { name: "Remove deadline", exact: true }).click();
    await expect(actionRow(page).getByRole("alert")).toBeVisible();
    expect(state.deadline).toBe("2027-12-20T09:00:00Z");
    state.fail = false;
    await actionRow(page).getByRole("button", { name: "Retry", exact: true }).click();
    await expect(actionRow(page).getByRole("button", { name: "Set deadline", exact: true })).toBeEnabled();
    await actionRow(page).getByRole("button", { name: "Remove deadline", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(2);
    expect(state.writes.map((write) => write.body)).toEqual([{ deadline_at: "" }, { deadline_at: "" }]);
});

test("late successful completion cannot refresh or expose actions in a replacement session", async ({ page }) => {
    const state = await actionTransport(page);
    await openActions(page);
    state.holdWrite = true;
    await actionRow(page).getByRole("button", { name: "Complete check card", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(1);
    await page.evaluate(() => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.newIdentity());
    await expect(page.getByRole("button", { name: "All work 65", exact: true })).toBeVisible();
    const currentRequests = (await workRequests(page)).length;
    state.holdWrite = false;
    state.release();
    await expect(page.getByRole("button", { name: "Complete check card", exact: true })).toHaveCount(0);
    expect(await workRequests(page)).toHaveLength(currentRequests);
});

const workflowColumns = (project = "6KZLKlukzZm") => [
    { uid: "column", project_uid: project, name: "Doing", is_archive: false, workflow_counts_as_completed: false },
    { uid: "done", project_uid: project, name: "Done", is_archive: false, workflow_counts_as_completed: true },
];
async function ordinaryTransport(page: Page) {
    const state = await actionTransport(page);
    state.check = false;
    state.columns = workflowColumns();
    return state;
}

test("ordinary completion requests columns lazily, moves at native index zero and rereads actual workflow and counts", async ({ page }) => {
    const state = await ordinaryTransport(page);
    await page.goto(`${fixture}?entry=/dashboard`);
    expect(state.reads).toBe(0);
    await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
    await expect(actionRow(page).getByRole("button", { name: "Complete card", exact: true })).toBeEnabled();
    const before = (await workRequests(page)).length;
    await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
    await expect.poll(() => state.reads).toBe(3);
    expect(state.writes).toEqual([{ path: expect.stringMatching(/\/order$/), body: { parent_uid: "done", order: 0 } }]);
    await expect.poll(async () => (await workRequests(page)).length).toBe(before + 1);
    expect(state.sourceColumn).toBe("done");
    const details = await page.evaluate(() =>
        (window as unknown as { myWorkFixture: Fixture }).myWorkFixture.requests.filter((entry) => entry.params?.sections)
    );
    expect(details.every((entry) => entry.params.sections === "checklists,project_columns")).toBe(true);
});

test("multiple completion columns require named choice and preserve chosen native destination", async ({ page }) => {
    const state = await ordinaryTransport(page);
    state.columns!.push({ ...state.columns![1], uid: "accepted", name: "Accepted" });
    await openActions(page);
    await expect(actionRow(page).getByRole("button", { name: "Complete card", exact: true })).toBeDisabled();
    await actionRow(page).getByRole("combobox", { name: "Completion column", exact: true }).selectOption({ label: "Accepted" });
    await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(1);
    expect(state.writes[0].body).toEqual({ parent_uid: "accepted", order: 0 });
});

for (const invalid of [
    "zero",
    "omitted",
    "malformed",
    "archive-source",
    "archive-target",
    "current",
    "wrong-project",
    "invalid-uid",
    "duplicate-uid",
    "missing-check",
    "missing-state",
    "malformed-state",
    "already-completed",
    "app",
    "linked",
]) {
    test(`ordinary completion excludes ${invalid} workflow facts`, async ({ page }) => {
        const state = await ordinaryTransport(page);
        if (invalid === "zero") state.columns = [state.columns![0]];
        if (invalid === "omitted") state.columns = undefined;
        if (invalid === "malformed") state.columns![1].workflow_counts_as_completed = "true";
        if (invalid === "archive-source") state.columns![0].is_archive = true;
        if (invalid === "archive-target") state.columns![1].is_archive = true;
        if (invalid === "current") state.sourceColumn = "done";
        if (invalid === "wrong-project") state.columns![1].project_uid = "foreign";
        if (invalid === "invalid-uid") state.columns![1].uid = "../bad";
        if (invalid === "duplicate-uid") state.columns!.push({ ...state.columns![1] });
        if (invalid === "missing-check") state.omit = ["is_check_card"];
        if (invalid === "missing-state") state.omit = ["work_state"];
        if (invalid === "malformed-state") state.workflowCompleted = "false";
        if (invalid === "already-completed") state.workflowCompleted = true;
        if (invalid === "app") state.native = false;
        if (invalid === "linked") state.linked = true;
        await page.goto(`${fixture}?entry=/dashboard`);
        await actionRow(page).getByRole("button", { name: "Card actions", exact: true }).click();
        await expect.poll(() => state.reads).toBe(1);
        await expect(actionRow(page).getByRole("button", { name: "Complete card", exact: true }).and(page.locator(":enabled"))).toHaveCount(0);
        expect(state.writes).toHaveLength(0);
    });
}

for (const stale of [
    "flag",
    "omitted-flag",
    "malformed-flag",
    "source",
    "other-source",
    "roles",
    "check",
    "columns",
    "state",
    "malformed-state",
    "completed",
]) {
    test(`fresh ordinary authorization rejects stale ${stale} despite old merged model grant`, async ({ page }) => {
        const state = await ordinaryTransport(page);
        await openActions(page);
        if (stale === "flag") state.columns![1].workflow_counts_as_completed = false;
        if (stale === "omitted-flag") delete state.columns![1].workflow_counts_as_completed;
        if (stale === "malformed-flag") state.columns![1].workflow_counts_as_completed = 1;
        if (stale === "source") state.sourceColumn = "done";
        if (stale === "other-source") {
            state.columns!.push({ ...state.columns![0], uid: "other-source" });
            state.sourceColumn = "other-source";
        }
        if (stale === "state") state.omit = ["work_state"];
        if (stale === "malformed-state") state.workflowCompleted = "false";
        if (stale === "completed") state.workflowCompleted = true;
        if (stale === "roles") state.omit = ["current_auth_role_actions"];
        if (stale === "check") state.omit = ["is_check_card"];
        if (stale === "columns") state.columns = undefined;
        await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
        await expect(actionRow(page).getByRole("alert")).toBeVisible();
        expect(state.writes).toHaveLength(0);
    });
}

for (const decision of ["Move only this card", "Move all descendants", "Cancel"]) {
    test(`ordinary native descendant confirmation supports ${decision}`, async ({ page }) => {
        const state = await ordinaryTransport(page);
        state.confirmation = true;
        await openActions(page);
        await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
        await page.getByRole("button", { name: decision, exact: true }).click();
        if (decision === "Cancel") {
            await expect(actionRow(page).getByRole("alert")).toBeVisible();
            expect(state.writes).toHaveLength(1);
        } else {
            await expect.poll(() => state.writes.length).toBe(2);
            expect(state.writes[1].body).toEqual({
                parent_uid: "done",
                order: 0,
                move_descendants: decision === "Move all descendants",
                confirmation_revision: "revision-1",
            });
        }
    });
}

for (const change of ["switchActor", "newIdentity", "refreshToken"] as const) {
    test(`ordinary confirmation cannot follow up after ${change}`, async ({ page }) => {
        const state = await ordinaryTransport(page);
        state.confirmation = true;
        await openActions(page);
        await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
        await expect(page.getByRole("button", { name: "Move all descendants", exact: true })).toBeVisible();
        await page.evaluate((change) => (window as unknown as { myWorkFixture: Fixture }).myWorkFixture[change](), change);
        await expect(page.getByRole("button", { name: "Move all descendants", exact: true })).toHaveCount(0);
        expect(state.writes).toHaveLength(1);
    });
}

test("ordinary duplicate click shares lock and 409 never retries movement", async ({ page }) => {
    const state = await ordinaryTransport(page);
    await openActions(page);
    state.hold = true;
    await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).dblclick();
    await expect.poll(() => state.reads).toBe(2);
    await expect(actionRow(page).getByRole("button", { name: "Complete card", exact: true })).toBeDisabled();
    state.conflict = true;
    state.hold = false;
    state.release();
    await expect(actionRow(page).getByRole("alert")).toBeVisible();
    expect(state.writes).toHaveLength(1);
});

test("policy changed during movement rereads actual state without claiming completed success", async ({ page }) => {
    const state = await ordinaryTransport(page);
    await openActions(page);
    state.holdWrite = true;
    await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(1);
    state.columns![1].workflow_counts_as_completed = false;
    state.holdWrite = false;
    state.release();
    await expect.poll(() => state.reads).toBe(3);
    expect(state.workflowCompleted).toBe(false);
    await expect(page.getByText("Card completed", { exact: true })).toHaveCount(0);
});

test("ordinary confirmation unmount cancels followup transport", async ({ page }) => {
    const state = await ordinaryTransport(page);
    state.confirmation = true;
    await openActions(page);
    await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Move all descendants", exact: true })).toBeVisible();
    await page.evaluate(() => {
        window.history.pushState(null, "", "/board/6KZLKlukzZm");
        window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page.getByRole("button", { name: "Move all descendants", exact: true })).toHaveCount(0);
    expect(state.writes).toHaveLength(1);
});

for (const mode of ["revoke", "cancel", "legacy"]) {
    test(`shared movement hook ${mode} guards every confirmation followup`, async ({ page }) => {
        const state = await actionTransport(page);
        state.confirmation = true;
        await page.goto(`${fixture}?entry=/movement-${mode === "legacy" ? "legacy" : "guard"}`);
        await page.getByRole("button", { name: "Run movement", exact: true }).click();
        await expect(page.getByRole("button", { name: "Move all descendants", exact: true })).toBeVisible();
        if (mode !== "legacy")
            await page.evaluate((mode) => {
                const fixture = (window as unknown as { movementGuardFixture: { revoke: () => void; cancel: () => void } }).movementGuardFixture;
                fixture[mode as "revoke" | "cancel"]();
            }, mode);
        if (mode !== "cancel") await page.getByRole("button", { name: "Move all descendants", exact: true }).click();
        await expect(page.getByRole("button", { name: "Move all descendants", exact: true })).toHaveCount(0);
        expect(state.writes).toHaveLength(mode === "legacy" ? 2 : 1);
    });
}

test("ordinary completion rereads actual state before row reset can cancel the detail query", async ({ page }) => {
    const state = await ordinaryTransport(page);
    await openActions(page);
    const before = (await workRequests(page)).length;
    state.holdRead = 3;
    await actionRow(page).getByRole("button", { name: "Complete card", exact: true }).click();
    await expect.poll(() => state.reads).toBe(3);
    expect(await workRequests(page)).toHaveLength(before);
    await expect(actionRow(page).getByRole("button", { name: "Complete card", exact: true })).toBeDisabled();
    state.holdRead = 0;
    state.release();
    await expect.poll(async () => (await workRequests(page)).length).toBe(before + 1);
    expect(state.writes).toHaveLength(1);
});
