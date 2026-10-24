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
