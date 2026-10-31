import { SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import { hasPanelAccess } from "../../core/apps/PanelAccess";
import type { CatalogApp } from "../../controllers/api/board/useBoardAppCatalog";
import type { Page, Route, WebSocketRoute } from "@playwright/test";
import { expect, test } from "@playwright/test";

test("Changes rail counts native unread cards and reads summary pages across board and identity transitions", async ({ page }) => {
    await page.routeWebSocket(/.*/, () => {});
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const now = new Date().toISOString();
    const changedAt = new Date(Date.now() - 120_000).toISOString();
    let actor = "fixture-user";
    let holdCards = false;
    let releaseCards: (() => void) | undefined;
    const cardReads: string[] = [];
    const user = () => ({ uid: actor, created_at: now, updated_at: now, firstname: "Fixture", lastname: "User", user_groups: [] });
    const projects = () =>
        ["left", "right"].map((uid) => ({
            uid,
            title: `${uid} board`,
            created_at: now,
            updated_at: now,
            owner_uid: actor,
            project_type: "default",
            archive_visible_days: 30,
            all_members: [user()],
            invited_member_uids: [],
            internal_bots: [],
            internal_bot_settings: {},
            current_auth_role_actions: ["*"],
            labels: [],
            description: "",
        }));
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const path = new URL(route.request().url()).pathname;
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            const uid = path.split("/")[2];
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user: user(), bots: [] };
            else if (path === `/board/${uid}`) json = { project: projects().find((project) => project.uid === uid) };
            else if (path === `/board/${uid}/cards`) {
                cardReads.push(`${actor}/${uid}`);
                if (holdCards)
                    await new Promise<void>((resolve) => {
                        releaseCards = resolve;
                    });
                const count = actor === "fixture-user" ? (uid === "left" ? 55 : 2) : 3;
                json = {
                    cards: Array.from({ length: count + 3 }, (_, index) => ({
                        uid: `${uid}-${index}`,
                        project_uid: uid,
                        project_column_uid: `${uid}-column`,
                        title: `${uid} card ${index}`,
                        created_at: now,
                        updated_at: now,
                        order: index,
                        member_uids: [],
                        labels: [],
                        relationships: [],
                        has_unread_change: index !== count + 2,
                        last_change_seq: index + 1,
                        last_change_at: index === count - 1 ? changedAt : index === count - 2 ? "invalid-date" : null,
                        ...(index === count ? { source_type: "project_card" } : index === count + 1 ? { source_type: "project_wiki" } : {}),
                    })),
                    columns: [{ uid: `${uid}-column`, project_uid: uid, name: `${uid} column`, order: 0, created_at: now, updated_at: now }],
                    checklists: [],
                    global_relationships: [],
                };
            } else if (path === `/board/${uid}/unread-card-summaries`) {
                const cursor = new URL(route.request().url()).searchParams.get("cursor");
                const count = actor === "fixture-user" ? (uid === "left" ? 55 : 2) : 3;
                const start = cursor ? 50 : 0;
                json = {
                    items: Array.from({ length: Math.min(50, count - start) }, (_, offset) => {
                        const index = start + offset;
                        return {
                            uid: `${uid}-${index}`,
                            project_uid: uid,
                            title: `${uid} card ${index}`,
                            has_unread_change: true,
                            unread_change_count: index === 0 ? null : index + 1,
                            unread_change_count_status: index === 0 ? "manual_unread" : "exact",
                            latest_change: { seq: index + 1, target_type: "comment", target_uid: `comment-${index}`, at: changedAt },
                        };
                    }),
                    next_cursor: count > 50 && !cursor ? "page-two" : null,
                    limit: 50,
                };
            } else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project")) json = { projects: projects(), columns: [], records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            await route.fulfill({ json });
        }
    );
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    const changes = page.getByRole("button", { name: "Changes", exact: true });
    await expect(changes).toContainText("55", { timeout: 45_000 });
    const statusFooter = page.getByRole("contentinfo", { name: "Workbench status" });
    await expect(statusFooter).toContainText("left board");
    await expect(statusFooter).toContainText("Unread: 55");
    await expect(statusFooter).toContainText("Members: 1");
    await expect(statusFooter).toContainText("Permissions: Read, Update, Card Write, Card Update, Card Delete");
    expect(cardReads).toEqual(["fixture-user/left"]);
    await expect(page.getByRole("region", { name: "Changes", exact: true })).toHaveCount(0);
    await page.evaluate(async () => {
        const path = "/src/core/models/index.ts";
        const { ProjectCard } = await import(/* @vite-ignore */ path);
        ProjectCard.Model.getModel("left-0").has_unread_change = false;
    });
    await expect(changes).toContainText("54");
    await expect(statusFooter).toContainText("Unread: 54");
    await page.evaluate(async () => {
        const path = "/src/core/models/index.ts";
        const { ProjectCard } = await import(/* @vite-ignore */ path);
        ProjectCard.Model.getModel("left-0").has_unread_change = true;
    });
    await expect(changes).toContainText("55");
    await changes.click();
    const sidebar = page.getByRole("region", { name: "Changes", exact: true }).last();
    await expect(sidebar.getByRole("button").filter({ hasText: "left card" })).toHaveCount(50);
    await expect(sidebar.getByText("Marked unread", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("Comment", { exact: true })).toHaveCount(50);
    await sidebar.getByRole("button", { name: "More", exact: true }).click();
    await expect(sidebar.getByRole("button").filter({ hasText: "left card" })).toHaveCount(5);
    await expect(sidebar.getByRole("button", { name: "More", exact: true })).toHaveCount(0);
    const changedRow = sidebar.getByRole("button").filter({ hasText: "left card 54" });
    await expect(changedRow.locator("time")).toHaveAttribute("datetime", changedAt);
    await expect(changedRow.locator("time")).toContainText("minutes ago");
    await expect(changedRow.locator("[data-unread-change-count]")).toContainText("55");
    // Native model totals and server summary pages have independent authority.
    await page.evaluate(async () => {
        const path = "/src/core/models/index.ts";
        const { ProjectCard } = await import(/* @vite-ignore */ path);
        ProjectCard.Model.getModels(
            (card: { project_uid: string; source_type?: string }) => card.project_uid === "left" && !card.source_type
        ).forEach((card: { has_unread_change: boolean }) => {
            card.has_unread_change = false;
        });
    });
    await expect(changes).toHaveText("");
    await expect(changedRow).toBeVisible();
    await page.getByRole("button", { name: "Explorer", exact: true }).click();
    holdCards = true;
    await page.locator("[data-workbench-explorer]").getByRole("button", { name: "right board", exact: true }).first().click();
    await expect.poll(() => releaseCards !== undefined).toBe(true);
    await expect(changes).toHaveText("");
    await expect(statusFooter).not.toContainText("Unread: 55");
    holdCards = false;
    releaseCards!();
    await expect(changes).toContainText("2");
    await expect(statusFooter).toContainText("right board");
    await expect(statusFooter).toContainText("Unread: 2");
    // Cached models select the current board immediately; the application cache also refreshes stale requests.
    const explorer = page.locator("[data-workbench-explorer]");
    await explorer.getByRole("button", { name: "left board", exact: true }).first().click();
    await expect(changes).toHaveText("");
    await explorer.getByRole("button", { name: "right board", exact: true }).first().click();
    await expect(changes).toContainText("2");
    expect(cardReads).toEqual(["fixture-user/left", "fixture-user/right", "fixture-user/left", "fixture-user/right"]);
    releaseCards = undefined;
    holdCards = true;
    actor = "other-user";
    await page.evaluate(async () => {
        const storePath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ storePath), import(/* @vite-ignore */ apiPath)]);
        await store.getState().updateToken("other-session", api);
    });
    await expect.poll(() => releaseCards !== undefined).toBe(true);
    await expect(changes).toHaveText("");
    await expect(statusFooter.locator("[data-status-project]")).toHaveCount(0);
    holdCards = false;
    releaseCards!();
    await expect(changes).toContainText("3");
    await expect(statusFooter).toContainText("Unread: 3");
    await expect(statusFooter).toContainText("Realtime: Socket open");
    releaseCards = undefined;
    holdCards = true;
    await page.evaluate(async () => {
        const storePath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ storePath), import(/* @vite-ignore */ apiPath)]);
        await store.getState().updateToken("renewed-session", api);
    });
    await expect.poll(() => releaseCards !== undefined).toBe(true);
    await expect(changes).toHaveText("");
    await expect(statusFooter.locator("[data-status-project]")).toHaveCount(0);
    holdCards = false;
    releaseCards!();
    await expect(changes).toContainText("3");
    expect(cardReads).toEqual([
        "fixture-user/left",
        "fixture-user/right",
        "fixture-user/left",
        "fixture-user/right",
        "other-user/right",
        "other-user/right",
    ]);
    const transfer = await page.evaluateHandle(() => {
        const data = new DataTransfer();
        data.setData("application/x-langboard-board-reference", "left");
        return data;
    });
    await page.locator("[data-board-pane]").first().dispatchEvent("drop", { dataTransfer: transfer });
    const panes = page.locator("[data-board-pane]");
    await expect(panes).toHaveCount(2);
    await panes.nth(1).dispatchEvent("pointerdown");
    await expect(statusFooter).toContainText("left board");
    for (const paneFooter of await page.locator("[data-board-pane-footer]").all()) {
        const bounds = await paneFooter.boundingBox();
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual((await statusFooter.boundingBox())!.y);
    }
    await panes.first().dispatchEvent("pointerdown");
    await expect(statusFooter).toContainText("right board");
    await page.getByRole("button", { name: "Close left board", exact: true }).click();
    await expect(panes).toHaveCount(1);
    for (const width of [390, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await expect(statusFooter).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        const bounds = await statusFooter.boundingBox();
        expect(bounds!.width).toBe(width);
        const realtime = await statusFooter.locator("[data-status-realtime]").boundingBox();
        expect(realtime!.x).toBeGreaterThanOrEqual(0);
        expect(realtime!.x + realtime!.width).toBeLessThanOrEqual(width);
        const dock = page.locator("[data-floating-nav-content]").first();
        await expect(dock).toBeVisible();
        expect((await dock.boundingBox())!.y + (await dock.boundingBox())!.height).toBeLessThanOrEqual(bounds!.y);
        await page.screenshot({ path: test.info().outputPath(`board-${width}.png`) });
    }

    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const { default: store } = await import(/* @vite-ignore */ path);
        store.getState().removeToken();
    });
    await expect(statusFooter.locator("[data-status-project]")).toHaveCount(0);
    expect(errors).toEqual([]);
});

test.beforeEach(async ({ page }) => {
    const accounts = new Map<string, { revision: number; pinned_apps: { key: string; name: string }[] }>();
    await page.route("**/account/app-preferences", (route) => {
        const actor = new URL(page.url()).searchParams.get("actor") ?? "fixture-user";
        const current = accounts.get(actor) ?? { revision: 0, pinned_apps: [] };
        if (route.request().method() === "PUT") {
            const input = route.request().postDataJSON();
            if (input.expected_revision !== current.revision) return route.fulfill({ status: 409, json: {} });
            accounts.set(actor, { revision: current.revision + 1, pinned_apps: input.pinned_apps });
        }
        return route.fulfill({ json: accounts.get(actor) ?? current });
    });
});

for (const width of [1440, 390]) {
    test(`outline follows the current card attachment models at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/src/components/Layout/outline-model.fixture.html");
        const attachmentSection = page.getByRole("button", { name: "Attached files 1", exact: true });
        await expect(page.getByText("first", { exact: true })).toBeVisible();
        await expect(attachmentSection).toHaveCount(0);
        await page.getByRole("button", { name: "Switch card" }).click();
        await expect(page.getByText("second", { exact: true })).toBeVisible();
        await expect(attachmentSection).toBeVisible();
        await page.getByRole("button", { name: "Switch card" }).click();
        await expect(page.getByText("first", { exact: true })).toBeVisible();
        await expect(attachmentSection).toHaveCount(0);
    });
}

test("route changes retain the physical shell and preserve page context in its sidebar", async ({ page }) => {
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
    // Browser-owned references detect replacement even when the new DOM looks identical.
    const nodes = await Promise.all([
        page.locator("header").elementHandle(),
        page.getByRole("navigation", { name: "Workspace", exact: true }).elementHandle(),
        page.locator("aside").elementHandle(),
    ]);
    for (const name of ["Board", "Card", "Wiki", "Dashboard"]) {
        await page.getByRole("link", { name: `Open ${name}`, exact: true }).click();
        await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
        await expect(page.getByText(`Sidebar ${name}`, { exact: true })).toBeVisible();
        for (const node of nodes) expect(await node!.evaluate((element) => element.isConnected)).toBe(true);
        await expect(page.locator("[inert]")).toHaveCount(0);
    }
    const home = page.getByRole("link", { name: "Go to Dashboard", exact: true });
    await expect(home).toHaveAttribute("href", "/dashboard/projects/all");
    await home.focus();
    await expect(home).toBeFocused();
    await home.press("Enter");
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
});

test("mobile rail actions are keyboard reachable and open context content", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");

    const explorer = page.getByRole("button", { name: "Explorer", exact: true });
    await explorer.focus();
    await explorer.press("Enter");
    await expect(page.getByText("Sidebar Dashboard", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByText("Sidebar Dashboard", { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

// Initial page registration must not move the outlet into a new React subtree.
test("initial shell registration mounts the page once", async ({ page }) => {
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
    await expect(page.getByTestId("page-mounts")).toHaveText("1");
});

test("mobile mode selection enters the panel, and explicit dismissal restores navigation focus", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const trigger = page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "Explorer", exact: true });
    await trigger.press("Enter");
    const panel = page.getByRole("complementary", { name: "Explorer", exact: true });
    await expect(panel).toBeFocused();
    await panel.press("Tab");
    const close = panel.getByRole("button", { name: "Close", exact: true });
    await expect(close).toBeFocused();
    await close.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await trigger.press("Enter");
    await expect(panel).toBeFocused();
    await panel.press("Tab");
    await close.press("Enter");
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
    // Cancelling overflow keeps normal trigger restoration.
    const more = page.getByRole("button", { name: "More", exact: true });
    await more.press("Enter");
    await page.getByRole("menu").press("Escape");
    await expect(more).toBeFocused();
});

test("a nested mobile menu owns Escape before the context panel", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    await page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "Explorer", exact: true }).press("Enter");
    const panel = page.getByRole("complementary", { name: "Explorer", exact: true });
    await expect(panel).toBeFocused();
    await panel.getByRole("button", { name: "Nested menu", exact: true }).press("Enter");
    await expect(page.getByRole("menuitem", { name: "Nested item", exact: true })).toBeVisible();
    await page.getByRole("menuitem", { name: "Nested item", exact: true }).press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("button", { name: "Nested menu", exact: true })).toBeFocused();
});

test("project navigation focuses the existing palette trigger after the new configuration is ready", async ({ page }) => {
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    await page.getByRole("link", { name: "Open Board", exact: true }).press("Enter");
    await expect(page.getByRole("heading", { name: "Board", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Command palette", exact: true })).toBeFocused();
});

test("Escape from page-owned portaled controls dismisses the physical mobile panel", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const trigger = page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "Explorer", exact: true });
    await trigger.press("Enter");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const panel = page.getByRole("complementary", { name: "Explorer", exact: true });
    await expect(panel).toBeFocused();
    await panel.press("Tab");
    await expect(panel.getByRole("button", { name: "Close", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    const label = panel.getByRole("button", { name: "🧩 Contract", exact: true });
    await expect(label).toBeFocused();
    await label.press("Enter");
    await expect(label).toHaveAttribute("aria-expanded", "true");
    await label.press("Escape");
    await expect(label).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("[data-compact-label-preview]")).toHaveCount(0);
    await expect(panel).toBeVisible();
    await expect(label).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(panel.getByRole("textbox", { name: "Sidebar search", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(panel.getByRole("button", { name: "Nested menu", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
});

test("same workspace navigation preserves sidebar state", async ({ page }) => {
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    await page.getByRole("link", { name: "Open Board", exact: true }).click();
    await page.getByRole("textbox", { name: "Sidebar search", exact: true }).fill("retained query");
    await page.getByRole("link", { name: "Open Card", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Card", exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Sidebar search", exact: true })).toHaveValue("retained query");
    await expect(page.locator("[inert]")).toHaveCount(0);
});

test("split-window Explorer collapses automatically and can be expanded or collapsed explicitly", async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 800 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const sidebar = page.locator("[id^='resizable-sidebar-']").filter({ has: page.getByText("Sidebar Dashboard", { exact: true }) });
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect.poll(() => sidebar.evaluate((element) => element.getBoundingClientRect().width)).toBe(28);
    const toggle = sidebar.getByRole("button").last();
    await toggle.click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect(page.getByText("Sidebar Dashboard", { exact: true })).toBeVisible();
    const bounds = await toggle.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(1100);
    await toggle.click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await toggle.click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1100);
});

test("comment layout follows card width even within a wide viewport and reserves action space", async ({ page }) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto("/src/pages/BoardPage/components/card/card-comment-layout.fixture.html");
    const mode = page.getByTestId("comment-layout");
    await expect(mode).toHaveText("mobile");
    await page.getByRole("button", { name: "Wide card" }).click();
    await expect(mode).toHaveText("panel");
    await page.getByRole("button", { name: "Toggle actions" }).click();
    await expect(mode).toHaveText("mobile");
    await page.getByRole("button", { name: "Toggle actions" }).click();
    await expect(mode).toHaveText("panel");
    await page.getByRole("button", { name: "Narrow card" }).click();
    await expect(mode).toHaveText("mobile");
});

test("description markers remain within the card viewport and track its internal scroll", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/src/pages/BoardPage/components/card/description/description-scroll.fixture.html");
    const viewport = page.locator("[data-card-content-viewport]");
    const markers = page.getByRole("button", { name: /Go to description|번째 설명으로 이동/ });
    // The 360px native viewport groups 60 chunks into 20 bounded markers.
    await expect(markers).toHaveCount(20);
    const first = markers.first();
    const bounds = await viewport.boundingBox();
    const initial = await first.boundingBox();
    expect(initial!.y).toBeGreaterThanOrEqual(bounds!.y);
    expect(initial!.y + initial!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
    await viewport.focus();
    await viewport.press("PageDown");
    await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    // A page may stay inside one grouped marker; End must reach a different range.
    await viewport.press("End");
    await expect
        .poll(async () => page.locator("button[aria-current='location']").getAttribute("aria-label"))
        .not.toBe(await first.getAttribute("aria-label"));
    await markers.nth(11).click();
    await expect
        .poll(() => page.locator("button[aria-current='location']").getAttribute("aria-label"))
        .toBe(await markers.nth(11).getAttribute("aria-label"));
    await page.getByRole("button", { name: "Resize card" }).click();
    await page.getByRole("button", { name: "Expand header" }).click();
    await markers.first().click();
    await expect(first).toHaveAttribute("aria-current", "location");
    const rail = page.locator("[data-card-description-rail]");
    const beforeEnd = await rail.boundingBox();
    await markers.last().click();
    await expect
        .poll(async () => {
            const current = await rail.boundingBox();
            return !!current && Math.abs(current.y - beforeEnd!.y) < 1 && Math.abs(current.height - beforeEnd!.height) < 1;
        })
        .toBe(true);
    await expect(viewport).toHaveCSS("scrollbar-width", "none");
    await expect
        .poll(async () => {
            const viewportBounds = await viewport.boundingBox();
            const railBounds = await page.locator("[data-card-description-rail]").boundingBox();
            return (
                !!viewportBounds &&
                !!railBounds &&
                railBounds.y >= viewportBounds.y &&
                railBounds.y + railBounds.height <= viewportBounds.y + viewportBounds.height
            );
        })
        .toBe(true);
    await page.getByRole("button", { name: "Short description" }).press("Enter");
    await expect(markers).toHaveCount(2);
    await markers.last().click();
    await expect(markers.last()).toHaveAttribute("aria-current", "location");
});

test("expanded Explorer suppresses Activity Rail tooltip and collapsed Explorer restores it", async ({ page }) => {
    const warnings: string[] = [];
    page.on("console", (message) => {
        if (message.type() === "warning") warnings.push(message.text());
    });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const explorer = page.getByRole("button", { name: "Explorer", exact: true });
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.getByRole("button", { name: "Collapse", exact: true }).click();
    await page.getByRole("button", { name: "Expand", exact: true }).focus();
    const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
    if (await rail.count()) await expect(rail).toHaveAttribute("data-context-expanded", "false");
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toBeVisible();
    await expect(page.getByRole("tooltip")).toHaveText("Explorer");
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    expect(warnings.filter((message) => message.includes("controlled to uncontrolled") || message.includes("uncontrolled to controlled"))).toEqual(
        []
    );
});

test("common Sidebar tooltips follow explicit collapse state", async ({ page }) => {
    const warnings: string[] = [];
    page.on("console", (message) => {
        if (message.type() === "warning") warnings.push(message.text());
    });
    await page.goto("/src/components/Layout/sidebar-tooltip.fixture.html");
    const explorer = page.getByRole("link", { name: "Explorer", exact: true });
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.getByRole("button", { name: "Collapse", exact: true }).click();
    await page.getByRole("button", { name: "Expand", exact: true }).focus();
    const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
    if (await rail.count()) await expect(rail).toHaveAttribute("data-context-expanded", "false");
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toBeVisible();
    await expect(page.getByRole("tooltip")).toHaveText("Explorer");
    await explorer.click();
    await expect(page).toHaveURL(/#explorer$/);
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    expect(warnings.filter((message) => message.includes("controlled to uncontrolled") || message.includes("uncontrolled to controlled"))).toEqual(
        []
    );
});

test("mobile floating Sidebar retains tooltip and link navigation", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/components/Layout/sidebar-tooltip.fixture.html");
    const floating = page.locator(".floating-wrapper");
    await floating.locator("button svg").click();
    const explorer = floating.getByRole("link", { name: "Explorer", exact: true });
    await expect(explorer).toBeVisible();
    await explorer.focus();
    await expect(page.getByRole("tooltip")).toBeVisible();
    await expect(page.getByRole("tooltip")).toHaveText("Explorer");
    await explorer.click();
    await expect(page).toHaveURL(/#explorer$/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test("failed board list exposes retry and suppresses endless skeletons", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/board-load.fixture.html");
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
    const retry = page.getByRole("button", { name: /Retry|다시 시도/ });
    await retry.press("Enter");
    await expect(page.locator("output")).toHaveText("2");
    await expect(retry).toHaveCount(0);
    await expect(page.locator("output")).toHaveText("2");
    await page.getByRole("button", { name: "Release retry" }).click();
    await expect(retry).toBeEnabled();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
});

test("mobile description navigator fills a fixed viewport while keyboard scrolling remains available", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/pages/BoardPage/components/card/description/description-scroll.fixture.html");
    const viewport = page.locator("[data-card-content-viewport]");
    const rail = page.locator("[data-card-description-rail]");
    const markers = rail.getByRole("button");
    // The 360px native viewport groups 60 chunks into 20 bounded markers.
    await expect(markers).toHaveCount(20);
    await expect(rail).toBeVisible();
    const initial = await rail.boundingBox();
    await viewport.focus();
    await viewport.press("PageDown");
    await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    await markers.last().click();
    await expect
        .poll(async () => {
            const current = await rail.boundingBox();
            return !!current && Math.abs(current.y - initial!.y) < 1 && Math.abs(current.height - initial!.height) < 1;
        })
        .toBe(true);
    const bounds = await viewport.boundingBox();
    expect(initial!.y - bounds!.y).toBeGreaterThanOrEqual(24);
    expect(bounds!.y + bounds!.height - initial!.y - initial!.height).toBeGreaterThanOrEqual(24);
    expect(initial!.x + initial!.width).toBeLessThanOrEqual(bounds!.x);
    await expect(viewport).toHaveCSS("scrollbar-width", "none");
    await markers.last().press("Home");
    await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBe(0);
    await markers.first().press("PageDown");
    await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});

test.describe("compact label touch", () => {
    test.use({ hasTouch: true });
    test("compact child labels preserve dots, keyboard and touch expansion without row shifts", async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.emulateMedia({ reducedMotion: "reduce" });
        await page.goto("/src/components/LabelBadge/compact-labels.fixture.html");
        const global = page.getByRole("button", { name: "🧩 Contract", exact: true });
        const local = page.getByRole("button", { name: "Local", exact: true });
        await expect(global).toHaveText("🧩");
        await expect(local).toHaveText("");
        const before = await page.getByTestId("row").boundingBox();
        await global.focus();
        await expect(page.getByRole("dialog", { name: "🧩 Contract" })).toBeVisible();
        await expect(page.getByRole("dialog", { name: "🧩 Contract" })).toHaveCSS("animation-name", "none");
        expect(await page.getByTestId("row").boundingBox()).toEqual(before);
        await global.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await local.tap();
        await expect(page.getByRole("dialog", { name: "Local" })).toBeVisible();
        await page.getByRole("button", { name: "Outside" }).click();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(page.getByText("Ordinary", { exact: true })).toBeVisible();
    });
});

test("compact labels expand on hover and collapse without moving neighboring labels", async ({ page }) => {
    await page.goto("/src/components/LabelBadge/compact-labels.fixture.html");
    const label = page.getByRole("button", { name: "🧩 Contract", exact: true });
    const next = page.getByRole("button", { name: "Local", exact: true });
    const before = await next.boundingBox();
    await label.hover();
    await expect(page.getByRole("dialog", { name: "🧩 Contract" })).toBeVisible();
    expect(await next.boundingBox()).toEqual(before);
    await page.getByRole("button", { name: "Outside" }).hover();
    await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("compact sidebar label Escape collapses the label and preserves the open parent card", async ({ page }) => {
    await page.goto("/src/components/LabelBadge/compact-labels.fixture.html?nested=1");
    const label = page.getByRole("button", { name: "🧩 Contract", exact: true });
    await label.focus();
    await expect(page.getByRole("dialog", { name: "🧩 Contract", exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Parent card" })).toBeVisible();
    await label.press("Escape");
    await expect(page.getByRole("dialog", { name: "🧩 Contract", exact: true })).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Parent card" })).toBeVisible();
    await expect(label).toBeFocused();
    await page.getByRole("button", { name: "Parent action", exact: true }).press("Escape");
    await expect(page.getByRole("dialog", { name: "Parent card" })).toHaveCount(0);
});

test("hovered sidebar label consumes Escape when focus remains in the parent card", async ({ page }) => {
    await page.goto("/src/components/LabelBadge/compact-labels.fixture.html?nested=1");
    const label = page.getByRole("button", { name: "🧩 Contract", exact: true });
    await label.focus();
    const parent = page.getByRole("dialog", { name: "Parent card" });
    await expect(parent).toBeVisible();
    await page.getByRole("button", { name: "Parent action", exact: true }).focus();
    await label.hover();
    await expect(page.getByRole("dialog", { name: "🧩 Contract", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "🧩 Contract", exact: true })).toHaveCount(0);
    await expect(parent).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(parent).toHaveCount(0);
});

test("sidebar preview focus does not dismiss the parent card", async ({ page }) => {
    await page.goto("/src/components/LabelBadge/compact-labels.fixture.html?nested=1");
    const label = page.getByRole("button", { name: "🧩 Contract", exact: true });
    await label.focus();
    const parent = page.getByRole("dialog", { name: "Parent card" });
    await expect(parent).toBeVisible();
    const preview = page.getByRole("dialog", { name: "🧩 Contract", exact: true });
    await expect(preview).toBeVisible();
    await preview.focus();
    await expect(parent).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(preview).toHaveCount(0);
    await expect(parent).toBeVisible();
});

test("hovered sidebar preview wrapper does not intercept a click intended for its dot", async ({ page }) => {
    await page.goto("/src/components/LabelBadge/compact-labels.fixture.html?nested=1");
    const label = page.getByRole("button", { name: "🧩 Contract", exact: true });
    await label.focus();
    const parent = page.getByRole("dialog", { name: "Parent card" });
    await expect(parent).toBeVisible();
    await page.getByRole("button", { name: "Parent action", exact: true }).focus();
    await label.hover();
    const preview = page.getByRole("dialog", { name: "🧩 Contract", exact: true });
    await expect(preview).toBeVisible();
    await expect
        .poll(() =>
            label.evaluate((element) => {
                const rect = element.getBoundingClientRect();
                return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)?.closest("[data-compact-label]") === element;
            })
        )
        .toBe(true);
    await label.click();
    await expect(parent).toBeVisible();
    await label.press("Escape");
    await expect(preview).toHaveCount(0);
    await expect(parent).toBeVisible();
});

test.describe("mobile context label preview", () => {
    test.use({ hasTouch: true });
    test("touch preview paints above the context panel and outside tap dismisses only the label", async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html");

        await page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "Explorer", exact: true }).click();
        const panel = page.getByRole("complementary", { name: "Explorer", exact: true });
        await expect(panel).toBeFocused();
        await panel.getByRole("button", { name: "🧩 Contract", exact: true }).tap();
        const preview = page.getByRole("dialog", { name: "🧩 Contract", exact: true });
        await expect(preview).toBeVisible();
        const previewZ = await preview.evaluate((element) => Number(getComputedStyle(element).zIndex));
        const panelZ = await panel.evaluate((element) => Number(getComputedStyle(element).zIndex));
        expect(previewZ).toBeGreaterThan(panelZ);
        await panel.getByRole("textbox", { name: "Sidebar search", exact: true }).tap();
        await expect(preview).toHaveCount(0);
        await expect(panel).toBeVisible();
    });
});

for (const width of [1440, 390]) {
    test(`column event preserves source snapshots and keyboard evidence at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.route(/\/column-history(?:\?|$)/, (route) => route.fulfill({ json: { records: [] } }));
        await page.goto("/src/components/Layout/column-history.fixture.html?transitions");
        const timeline = page.getByRole("region", { name: "Comment event examples" });
        await expect(timeline.locator("time")).toHaveText([
            "History Worker · Review › Doing",
            "검토 봇 · Doing › Review",
            "Unknown User · Archived stage",
            "Unknown User · Created stage",
        ]);
        const entry = timeline.locator("time").filter({ hasText: /^History Worker/ });
        await expect(entry).toHaveAttribute("data-card-activity-uid", "user-move");
        await expect(entry).toHaveAttribute("datetime", "2026-10-01T12:00:00.000Z");
        await entry.focus();
        await expect(entry).toBeFocused();
        await expect(page.getByRole("tooltip")).toContainText("Review › Doing");
        await expect(page.getByRole("tooltip")).toContainText("History Worker");
        await expect(timeline.getByText("Not a source", { exact: false })).toHaveCount(0);
        await expect(timeline.getByText("Invalid timestamp", { exact: false })).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

    test(`column history hides the previous card while the next request is pending at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let pending: Route | undefined;
        const record = (name: string) => ({
            uid: name,
            activity_type: "card_moved",
            created_at: "2026-10-01T12:00:00Z",
            column: { name },
            recorder: { type: "user", firstname: "History", lastname: "Worker" },
        });
        await page.route(/\/column-history(?:\?|$)/, async (route) => {
            if (route.request().url().includes("history-second")) pending = route;
            else await route.fulfill({ json: { records: [record("First backlog"), record("First active"), record("First returned")] } });
        });
        await page.goto("/src/components/Layout/column-history.fixture.html");
        const timeline = page.getByRole("navigation", { name: "Activity", exact: true });
        await expect(timeline.locator("time")).toHaveText(["First backlog", "First active", "First returned"]);
        await expect(timeline.locator("time").first()).toHaveAttribute("aria-label", /History Worker/);
        await timeline.locator("time").first().focus();
        await expect(page.getByRole("tooltip")).toContainText("History Worker");
        await page.getByRole("button", { name: "Switch history card" }).click();
        await expect.poll(() => !!pending).toBe(true);
        await expect(page.getByText("First returned", { exact: true })).toHaveCount(0);
        await pending!.fulfill({ json: { records: [record("Second review")] } });
        await expect(timeline.locator("time")).toHaveText(["Second review"]);
    });

    test(`column history follows the selected app language without refetching at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let requests = 0;
        const createdAt = "2026-10-01T12:00:00Z";
        await page.route(/\/column-history(?:\?|$)/, async (route) => {
            requests += 1;
            await route.fulfill({
                json: {
                    records: [
                        {
                            uid: "localized-event",
                            activity_type: "card_moved",
                            created_at: createdAt,
                            column: { name: "Review queue" },
                            recorder: { type: "user", firstname: "History", lastname: "Worker" },
                        },
                    ],
                },
            });
        });
        await page.goto("/src/components/Layout/column-history.fixture.html");
        const entry = page.locator("nav time");
        await expect(entry).toHaveText("Review queue");
        await entry.focus();
        await expect(page.getByRole("tooltip")).toBeVisible();
        for (const language of ["ko-KR", "en-US", "ja-JP", "zh-CN"]) {
            const exact = await page.evaluate(
                async ({ language, createdAt }) => {
                    const modulePath = "/src/i18n.ts";
                    const { default: i18n } = await import(modulePath);
                    await i18n.changeLanguage(language);
                    return new Intl.DateTimeFormat(language, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(createdAt));
                },
                { language, createdAt }
            );
            await expect(entry).toHaveAttribute("aria-label", `Review queue · ${exact} · History Worker`);
            await expect(page.getByRole("tooltip")).toContainText(exact);
        }
        await entry.press("Escape");
        await expect(page.getByRole("tooltip")).toHaveCount(0);
        expect(requests).toBe(1);
    });
}

test("rail keeps settings reachable below scrolling workspace actions", async ({ page }) => {
    await page.setViewportSize({ width: 1071, height: 320 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
    const settings = rail.getByRole("button", { name: "App settings", exact: true });
    await expect(settings).toBeVisible();
    const railBox = (await rail.boundingBox())!;
    const settingsBox = (await settings.boundingBox())!;
    expect(railBox.y + railBox.height - settingsBox.y - settingsBox.height).toBeLessThan(12);
    await settings.focus();
    await expect(settings).toBeFocused();
});

for (const storageFailure of ["malformed", "null", "read-denied", "write-denied"] as const) {
    test(`settings storage ${storageFailure} does not prevent shell navigation`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.addInitScript((failure) => {
            const key = "user-settings-store";
            if (failure === "malformed" || failure === "null") localStorage.setItem(key, failure === "null" ? "null" : "{broken");
            if (failure === "read-denied") {
                const getItem = Storage.prototype.getItem;
                Storage.prototype.getItem = function (name) {
                    if (name === key) throw new DOMException("Storage unavailable", "SecurityError");
                    return getItem.call(this, name);
                };
            }
            if (failure === "write-denied") {
                const setItem = Storage.prototype.setItem;
                Storage.prototype.setItem = function (name, value) {
                    if (name === key) throw new DOMException("Storage full", "QuotaExceededError");
                    return setItem.call(this, name, value);
                };
            }
        }, storageFailure);
        await page.goto("/src/components/Layout/workbench-shell.fixture.html");
        await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
        if (storageFailure === "malformed" || storageFailure === "null") {
            expect(await page.evaluate(() => localStorage.getItem("user-settings-store"))).toBe(storageFailure === "null" ? "null" : "{broken");
        }
        const sidebar = page.locator("[data-workbench-sidebar]");
        await sidebar.getByRole("button", { name: /Collapse|접기/ }).click();
        await page.getByRole("link", { name: "Open Board", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Board", exact: true })).toBeVisible();
        await expect(sidebar).toHaveAttribute("data-collapsed", "true");
        expect(errors).toEqual([]);
    });
}

for (const width of [1440, 1100]) {
    test(`explicit sidebar preference survives navigation and reload at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html");
        const sidebar = page.locator("[data-workbench-sidebar]");
        if (width <= 1280) await sidebar.getByRole("button", { name: /Expand|펼치기/ }).click();
        await expect(sidebar).toHaveAttribute("data-collapsed", "false");
        await sidebar.getByRole("button", { name: /Collapse|접기/ }).click();
        await page.getByRole("link", { name: "Open Board", exact: true }).click();
        await expect(sidebar).toHaveAttribute("data-collapsed", "true");
        await page.reload();
        await expect(sidebar).toHaveAttribute("data-collapsed", "true");
        await sidebar.getByRole("button", { name: /Expand|펼치기/ }).click();
        await page.getByRole("link", { name: "Open Board", exact: true }).click();
        await expect(sidebar).toHaveAttribute("data-collapsed", "false");
        await page.reload();
        await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    });
}

test("sidebar preference belongs to the current user", async ({ page }) => {
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const sidebar = page.locator("[data-workbench-sidebar]");
    await sidebar.getByRole("button", { name: /Collapse|접기/ }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await page.getByRole("button", { name: "Use second user", exact: true }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await page.getByRole("button", { name: "Use first user", exact: true }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await page.getByRole("button", { name: "Use second user", exact: true }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
});

test("column history replaces bounded pages and returns to latest without accumulating DOM", async ({ page }) => {
    const cursors: (string | null)[] = [];
    await page.route(/\/column-history(?:\?|$)/, async (route) => {
        const before = new URL(route.request().url()).searchParams.get("before_activity_uid");
        cursors.push(before);
        const start = before ? 0 : 20;
        await route.fulfill({
            json: {
                records: Array.from({ length: 20 }, (_, index) => ({
                    uid: `event-${start + index}`,
                    activity_type: "card_moved",
                    created_at: "2026-10-01T12:00:00Z",
                    column: { name: `Stage ${start + index}` },
                })),
                next_cursor: before ? null : "event-20",
            },
        });
    });
    await page.goto("/src/components/Layout/column-history.fixture.html");
    const timeline = page.getByRole("navigation", { name: "Activity", exact: true });
    await expect(timeline.locator("time")).toHaveCount(20);
    await expect(timeline.locator("time").first()).toHaveText("Stage 20");
    await timeline.getByRole("button", { name: "Older history", exact: true }).click();
    await expect(timeline.locator("time").first()).toHaveText("Stage 0");
    await expect(timeline.locator("time")).toHaveCount(20);
    await expect(timeline.getByRole("button", { name: "Older history", exact: true })).toHaveCount(0);
    await timeline.getByRole("button", { name: "Latest history", exact: true }).click();
    await expect(timeline.locator("time").first()).toHaveText("Stage 20");
    await expect(timeline.locator("time")).toHaveCount(20);
    expect(cursors).toEqual([null, "event-20", null]);
});

for (const width of [360, 390, 412]) {
    test(`mobile rail reserves body space and overflow restores focus at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html");
        const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
        await expect(page.getByRole("button", { name: "Toggle navigation menu", exact: true })).toHaveCount(0);
        const bounds = await rail.boundingBox();
        expect(bounds!.y + bounds!.height).toBe(844);
        expect(bounds!.height).toBe(57);
        const body = await page.locator("main").boundingBox();
        expect(body!.y + body!.height).toBeLessThanOrEqual(bounds!.y);
        const dock = await page.getByRole("button", { name: "Work dock", exact: true }).boundingBox();
        expect(dock!.y + dock!.height).toBeLessThan(bounds!.y);
        for (const button of await rail.getByRole("button").all()) {
            const hit = await button.boundingBox();
            expect(hit!.width).toBeGreaterThanOrEqual(44);
            expect(hit!.height).toBeGreaterThanOrEqual(44);
        }
        const more = rail.getByRole("button", { name: "More", exact: true });
        await more.click();
        await expect(page.getByRole("menuitem", { name: "App settings", exact: true })).toBeVisible();
        await expect(page.getByRole("menuitem", { name: "Hidden action", exact: true })).toHaveCount(0);
        await page.getByRole("menuitem", { name: "App settings", exact: true }).press("Escape");
        await expect(more).toBeFocused();
        await more.click();
        await page.getByRole("menuitem", { name: "Relations", exact: true }).click();
        const panel = page.getByRole("complementary", { name: "Explorer", exact: true });
        await expect(panel).toBeFocused();
        await panel.getByRole("button", { name: "Close", exact: true }).click();
        await expect(more).toBeFocused();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });
}

for (const width of [390, 1440]) {
    test(`user app discovery stays separate from installation at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const requests: string[] = [];
        await page.route("**/apps/catalog*", async (route) => {
            requests.push(route.request().url());
            expect(route.request().method()).toBe("GET");
            const url = new URL(route.request().url());
            expect(url.searchParams.get("project_uid")).toBe("TestBoard01");
            await route.fulfill({
                json: {
                    items: [
                        {
                            key: "github-panel",
                            name: "GitHub",
                            description: "Approved integration",
                            version: "1.2.0",
                            is_installed: true,
                            capabilities: ["panels.render"],
                        },
                    ],
                    official: [{ key: "github-panel", name: "GitHub", description: "Official integration" }],
                    effective_mode: "approved_only",
                    can_manage_installation: false,
                    next_cursor: null,
                },
            });
        });
        await page.goto("/src/pages/AppsPage/AppStore.fixture.html");
        await expect(page.getByRole("link", { name: /GitHub/ })).toHaveCount(1);
        await expect(page.getByRole("link", { name: "Manage installation" })).toHaveCount(0);
        await page.getByRole("link", { name: /GitHub/ }).click();
        await expect(page.getByRole("heading", { name: "GitHub", exact: true })).toBeVisible();
        await expect(page.getByText("panels.render", { exact: true })).toBeVisible();
        expect(requests.some((url) => new URL(url).searchParams.get("key") === "github-panel")).toBe(true);
        await expect(page.getByRole("link", { name: "Configure on this board" })).toHaveAttribute("href", "/board/TestBoard01/settings/apps");
        await page.getByRole("link", { name: "All apps", exact: true }).click();
        await expect(page.getByRole("link", { name: /GitHub/ })).toHaveCount(1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("legacy store redirects into user discovery and honors disabled policy", async ({ page }) => {
    await page.route("**/apps/catalog*", (route) =>
        route.fulfill({ json: { items: [], official: [], effective_mode: "disabled", can_manage_installation: false, next_cursor: null } })
    );
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html?initial=" + encodeURIComponent("/settings/app-store"));
    await expect(page.getByText("Apps are disabled by the current policy.", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Manage installation" })).toHaveCount(0);
});

test("personal app pins persist, reorder, stay user scoped and grant no permissions", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const requests: string[] = [];
    await page.route("**/apps/catalog*", (route) => {
        requests.push(route.request().method());
        const key = new URL(route.request().url()).searchParams.get("key") ?? "alpha";
        return route.fulfill({
            json: {
                items: [{ key, name: key === "alpha" ? "Alpha" : "Beta", description: "Approved app", version: "1.0.0", is_installed: true }],
                official: [],
                effective_mode: "approved_only",
                can_manage_installation: false,
                next_cursor: null,
            },
        });
    });
    const fixture = "/src/pages/AppsPage/AppStore.fixture.html?initial=";
    await page.goto(fixture + encodeURIComponent("/apps/alpha"));
    await page.getByRole("button", { name: "Pin to rail", exact: true }).click();
    const shortcuts = page.getByRole("region", { name: "Your shortcuts", exact: true });
    await expect(shortcuts.getByRole("link", { name: "Alpha", exact: true })).toBeVisible();
    expect(requests).toEqual(["GET"]);
    await expect
        .poll(() =>
            page.evaluate(() =>
                JSON.parse(localStorage.getItem("user-settings-store") ?? "{}").pinned_apps?.["fixture-user"]?.map((pin: { key: string }) => pin.key)
            )
        )
        .toEqual(["alpha"]);
    await page.goto(fixture + encodeURIComponent("/apps/beta"));
    await page.getByRole("button", { name: "Pin to rail", exact: true }).click();
    await page.getByRole("button", { name: "Move Beta earlier", exact: true }).click();
    await expect(shortcuts.getByRole("link")).toHaveText(["Beta", "Alpha"]);
    await expect
        .poll(() =>
            page.evaluate(() =>
                JSON.parse(localStorage.getItem("user-settings-store") ?? "{}").pinned_apps?.["fixture-user"]?.map((pin: { key: string }) => pin.key)
            )
        )
        .toEqual(["beta", "alpha"]);
    await page.reload();
    await expect(shortcuts.getByRole("link")).toHaveText(["Beta", "Alpha"]);
    await expect(page.getByRole("button", { name: "Unpin from rail", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Remove Alpha shortcut", exact: true }).click();
    await expect(shortcuts.getByRole("link")).toHaveText(["Beta"]);
    await page.goto(fixture + encodeURIComponent("/apps/beta") + "&actor=other-user");
    await expect(shortcuts).toHaveCount(0);
    await page.getByRole("button", { name: "Pin to rail", exact: true }).click();
    await expect
        .poll(() => page.evaluate(() => Object.keys(JSON.parse(localStorage.getItem("user-settings-store") ?? "{}").pinned_apps ?? {}).sort()))
        .toEqual(["fixture-user", "other-user"]);
    expect(requests.every((method) => method === "GET")).toBe(true);
});

test("panel shortcuts require current policy, declaration and board consent", () => {
    const app: CatalogApp = {
        key: "example",
        name: "Example",
        version: "1.0.0",
        app_revision: null,
        is_available: true,
        panel: { url: "https://example.invalid/panel", name: "Example" },
        capabilities: ["panels.render"],
        workflow_requirements: null,
        resources: { selected_count: 0, access_counts: {}, health_counts: {}, connection_counts: {} },
        binding: {
            uid: "binding",
            revision: "revision",
            state: "enabled",
            granted_capabilities: ["panels.render"],
            stage_transitions_enabled: false,
        },
    };
    expect(hasPanelAccess(app)).toBe(true);
    for (const denied of [
        { ...app, is_available: false },
        { ...app, is_available: undefined },
        { ...app, panel: null },
        { ...app, capabilities: [] },
        { ...app, binding: null },
        { ...app, binding: { ...app.binding!, state: "disabled" } },
        { ...app, binding: { ...app.binding!, granted_capabilities: [] } },
    ])
        expect(hasPanelAccess(denied)).toBe(false);
});

test("stored shortcuts are bounded, normalized and never initialize panels", async ({ page }) => {
    await page.addInitScript(() =>
        localStorage.setItem(
            "user-settings-store",
            JSON.stringify({
                pinned_apps: {
                    "fixture-user": [
                        null,
                        { key: "../invalid", name: "Invalid" },
                        ...Array.from({ length: 35 }, (_, i) => ({ key: `app-${i}`, name: "Same name", ignored: "https://private.invalid" })),
                    ],
                },
            })
        )
    );
    let reads = 0;
    await page.route("**/apps/catalog*", (route) => {
        reads++;
        return route.fulfill({
            json: {
                items: [{ key: "alpha", name: "Alpha", description: "App", is_installed: true }],
                official: [],
                effective_mode: "approved_only",
                can_manage_installation: false,
                next_cursor: null,
            },
        });
    });
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html?initial=" + encodeURIComponent("/apps/alpha"));
    await expect(page.getByRole("region", { name: "Your shortcuts", exact: true }).getByRole("listitem")).toHaveCount(32);
    await expect(page.getByRole("button", { name: "Pin to rail", exact: true })).toBeDisabled();
    await expect(page.locator("iframe")).toHaveCount(0);
    expect(reads).toBe(1);
    const unicodeName = await page.evaluate(async () => {
        const path = "/src/core/stores/UserSettingsStore.ts";
        const { normalizePinnedApps } = await import(path);
        return normalizePinnedApps([{ key: "unicode", name: "🧩".repeat(81) }])[0].name;
    });
    expect(unicodeName).toBe("🧩".repeat(80));
});

test("account shortcuts restore on an isolated device and recover conflicts without replaying writes", async ({ page, browser }) => {
    let current = { revision: 2, pinned_apps: [{ key: "beta", name: "Beta" }] };
    let offline = false;
    let uncertain = false;
    let writes = 0;
    const fixture = "/src/pages/AppsPage/AppStore.fixture.html?initial=" + encodeURIComponent("/apps/alpha");
    const connect = async (device: Page) => {
        await device.route("**/account/app-preferences", (route) => {
            if (route.request().method() === "GET") {
                return offline ? route.abort("internetdisconnected") : route.fulfill({ json: current });
            }
            writes++;
            const input = route.request().postDataJSON();
            if (input.expected_revision !== current.revision) return route.fulfill({ status: 409, json: {} });
            current = { revision: current.revision + 1, pinned_apps: input.pinned_apps };
            if (uncertain) {
                uncertain = false;
                return route.abort("internetdisconnected");
            }
            return route.fulfill({ json: current });
        });
        await device.route("**/apps/catalog*", (route) =>
            route.fulfill({
                json: {
                    items: [{ key: "alpha", name: "Alpha", description: "Approved", is_installed: true }],
                    official: [],
                    next_cursor: null,
                    effective_mode: "approved_only",
                    can_manage_installation: false,
                },
            })
        );
    };
    await connect(page);
    await page.goto(fixture);
    const shortcuts = (device: Page) => device.getByRole("region", { name: "Your shortcuts", exact: true }).getByRole("link");
    await expect(shortcuts(page)).toHaveText(["Beta"]);
    expect(writes).toBe(0);
    await page.getByRole("button", { name: "Pin to rail", exact: true }).click();
    await expect(shortcuts(page)).toHaveText(["Beta", "Alpha"]);
    const other = await browser.newContext({ viewport: { width: 390, height: 844 } });
    try {
        const device = await other.newPage();
        await connect(device);
        await device.goto("http://127.0.0.1:4183" + fixture);
        await expect(shortcuts(device)).toHaveText(["Beta", "Alpha"]);
        expect(writes).toBe(1);
        current = { revision: 4, pinned_apps: [{ key: "beta", name: "Beta" }] };
        await page.getByRole("button", { name: "Remove Alpha shortcut", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Shortcut sync is unavailable");
        await expect(shortcuts(page)).toHaveText(["Beta"]);
        expect(writes).toBe(2);
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.getByRole("button", { name: "Pin to rail", exact: true })).toBeEnabled();
        expect(writes).toBe(2);
        // The server saves, but the response is lost: read back without replay.
        uncertain = true;
        await page.getByRole("button", { name: "Pin to rail", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(shortcuts(page)).toHaveText(["Beta", "Alpha"]);
        expect(writes).toBe(3);
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.getByRole("button", { name: "Unpin from rail", exact: true })).toBeEnabled();
        expect(writes).toBe(3);
        offline = true;
        await device.reload();
        await expect(device.getByRole("alert")).toContainText("Shortcut sync is unavailable");
        await expect(device.getByRole("button", { name: "Unpin from rail", exact: true })).toBeDisabled();
        offline = false;
        await device.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(device.getByRole("button", { name: "Unpin from rail", exact: true })).toBeEnabled();
        expect(writes).toBe(3);
        await expect(device.locator("iframe")).toHaveCount(0);
    } finally {
        await other.close();
    }
});

async function authenticateAddCardFixture(page: Page) {
    await page.evaluate(async () => {
        const storePath = "/src/core/stores/AuthStore.ts";
        const modelsPath = "/src/core/models/index.ts";
        const [{ default: store }, { AuthUser }] = await Promise.all([import(/* @vite-ignore */ storePath), import(/* @vite-ignore */ modelsPath)]);
        store.setState({ currentUser: AuthUser.Model.getModel("me"), state: "loaded", pageLoaded: true });
    });
}

for (const width of [1440, 390]) {
    for (const failure of ["abort", "server"] as const) {
        test(`card creation retains the draft after ${failure} at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            const requests: unknown[] = [];
            await page.route("**/board/fixture/card", async (route) => {
                requests.push(route.request().postDataJSON());
                if (requests.length === 1) {
                    if (failure === "abort") return route.abort("aborted");
                    return route.fulfill({ status: 500, json: { message: "Temporary failure" } });
                }
                return route.fulfill({ status: 201, json: { card: { uid: "created" } } });
            });
            await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?add-card=1");
            await authenticateAddCardFixture(page);
            await page.getByRole("button", { name: "Add a card", exact: true }).click();
            const title = "고객 요청 초안 — preserve on failure";
            const input = page.getByRole("textbox", { name: "Enter a title", exact: true });
            await input.fill(title);
            await input.press("Enter");
            await expect.poll(() => requests.length).toBe(1);
            await expect(input).toBeVisible();
            await expect(input).toBeEnabled();
            await expect(input).toHaveValue(title);
            await input.press("Enter");
            await expect.poll(() => requests.length).toBe(2);
            await expect(input).toHaveCount(0);
            expect(requests[1]).toEqual(requests[0]);
            expect(requests[0]).toEqual({ project_column_uid: "ready", title });
        });
    }
}

test("compose dock opens the first column editor and preserves a draft without submitting", async ({ page }) => {
    let writes = 0;
    await page.route("http://127.0.0.1:5381/**", async (route) => {
        if (route.request().method() === "POST") writes++;
        await route.fulfill({ json: { active_work: [], columns: [], pinned_apps: [], revision: 0 } });
    });
    await page.goto("/src/pages/BoardPage/components/board/BoardFilter.fixture.html?add-card=1");
    await authenticateAddCardFixture(page);
    const compose = page.getByRole("button", { name: "Compose", exact: true });
    await expect(compose).toBeVisible();
    await compose.click();
    const input = page.getByRole("textbox", { name: "Enter a title", exact: true });
    await expect(input).toBeFocused();
    await input.fill("Keep this card draft");
    await compose.click();
    await expect(input).toBeFocused();
    await expect(input).toHaveValue("Keep this card draft");
    expect(writes).toBe(0);
    await expect(page).not.toHaveURL(/new-card/);
});

test("resizing across the minimum threshold collapses and reverse dragging expands", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const sidebar = page.locator("[data-workbench-sidebar]").filter({ has: page.getByText("Sidebar Dashboard", { exact: true }) });
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    const resize = sidebar.locator(".resizer");
    const handle = await resize.boundingBox();
    await page.mouse.move(handle!.x + 1, handle!.y + 100);
    await page.mouse.down();
    await page.mouse.move(handle!.x - 220, handle!.y + 100, { steps: 12 });
    await page.mouse.up();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect.poll(() => sidebar.evaluate((element) => element.getBoundingClientRect().width)).toBe(28);
    const collapsed = await resize.boundingBox();
    await page.mouse.move(collapsed!.x + 1, collapsed!.y + 100);
    await page.mouse.down();
    await page.mouse.move(collapsed!.x + 240, collapsed!.y + 100, { steps: 12 });
    await page.mouse.up();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect(page.getByText("Sidebar Dashboard", { exact: true })).toBeVisible();
    const width = await sidebar.evaluate((element) => element.getBoundingClientRect().width);
    expect(width).toBeGreaterThanOrEqual(220);
    expect(width).toBeLessThanOrEqual(420);
    expect(await page.evaluate(() => document.documentElement.style.userSelect)).toBe("");
});

test("approved header orbit plays once on hover and respects reduced motion", async ({ page }) => {
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const home = page.getByRole("link", { name: "Go to Dashboard", exact: true });
    await expect(home.getByText("Langboard", { exact: true })).toBeVisible();
    await expect(home.locator("svg")).toHaveCount(1);
    const star = home.locator("g[id$=animated-star] > g");
    const position = () =>
        star.evaluate((element) => {
            const matrix = (element as SVGGraphicsElement).getCTM()!;
            return [matrix.e, matrix.f];
        });
    const resting = await position();
    await home.hover();
    await expect.poll(position).not.toEqual(resting);
    await expect.poll(position, { timeout: 3500 }).toEqual(resting);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await home.focus();
    await expect(home).toBeFocused();
    await expect(home.locator("g[id$=animated-star]")).toBeHidden();
    await expect(home.locator("g[id$=reduced-star]")).toBeVisible();
});

for (const width of [1440, 390]) {
    test(`status footer shares the shell without covering the dock at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html");
        const footer = page.getByRole("contentinfo", { name: "Workbench status" });
        await expect(footer).toBeVisible();
        await expect(footer).toContainText("MCP: Unverified");
        await expect(footer).toContainText("Realtime: Disconnected");
        const dock = page.locator("[data-floating-nav-content]");
        expect((await dock.boundingBox())!.y + (await dock.boundingBox())!.height).toBeLessThanOrEqual((await footer.boundingBox())!.y);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.getByRole("link", { name: "Open Board", exact: true }).click();
        await expect(footer).toHaveCount(1);
        await page.getByRole("link", { name: "Open Dashboard", exact: true }).click();
        await expect(footer.locator("[data-status-project]")).toHaveCount(0);
    });
}

test("footer observes actual transport events without adding startup requests", async ({ page }) => {
    let peer: WebSocketRoute | undefined;
    await page.routeWebSocket(/.*/, (socket) => {
        peer = socket;
    });
    const reads: string[] = [];
    page.on("request", (request) => {
        if (["fetch", "xhr"].includes(request.resourceType())) reads.push(request.url());
    });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const footer = page.getByRole("contentinfo", { name: "Workbench status" });
    await expect(footer).toContainText("Realtime: Disconnected");
    expect(reads).toEqual([]);
    await page.evaluate(async () => {
        const path = "/src/core/stores/socket/transport.ts";
        const { createSocketConnection } = await import(/* @vite-ignore */ path);
        createSocketConnection({
            props: {
                accessToken: "fixture",
                userUID: "sidebar-user-a",
                onOpen: async () => {},
                onMessage: async () => {},
                onClose: async () => {},
                onError: async () => {},
            },
            subscribe: () => {},
            onSubscribed: () => {},
            onUnsubscribed: () => {},
        });
    });
    await expect(footer).toContainText("Realtime: Socket open");
    peer!.close({ code: 1006 });
    await expect(footer).toContainText("Realtime: Connection closed");
    await page.getByRole("button", { name: "Use second user", exact: true }).click();
    await expect(footer).toContainText("Realtime: Disconnected");
    expect(reads).toEqual([]);
});

async function scheduleFixture(page: Page) {
    await page.routeWebSocket(/.*/, () => {});
    const now = new Date().toISOString();
    let actor = "fixture-user";
    const reads: URL[] = [];
    const exports: URL[] = [];
    const exportTokens: (string | undefined)[] = [];
    let fail = false;
    let exportFail = false;
    let hold = false;
    let release: (() => void) | undefined;
    const user = () => ({ uid: actor, created_at: now, updated_at: now, firstname: "Fixture", lastname: "User", user_groups: [] });
    const project = (uid = "left") => ({
        uid,
        title: `${uid} board`,
        created_at: now,
        updated_at: now,
        owner_uid: actor,
        project_type: "default",
        archive_visible_days: 30,
        all_members: [user()],
        invited_member_uids: [],
        internal_bots: [],
        internal_bot_settings: {},
        current_auth_role_actions: ["*"],
        labels: [],
        description: "",
    });
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const url = new URL(route.request().url());
            const path = url.pathname;
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            if (path === "/dashboard/schedule/ical") {
                exports.push(url);
                exportTokens.push(route.request().headers().authorization);
                if (hold)
                    await new Promise<void>((resolve) => {
                        release = resolve;
                    });
                return route.fulfill({
                    status: exportFail ? 409 : 200,
                    contentType: "text/calendar",
                    body: exportFail ? "export cap" : "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
                });
            }
            if (path === "/dashboard/schedule") {
                reads.push(url);
                const oldActor = actor;
                if (hold)
                    await new Promise<void>((resolve) => {
                        release = resolve;
                    });
                if (fail) return route.fulfill({ status: 400, json: { detail: "invalid range" } });
                const personal = url.searchParams.get("scope") === "personal";
                const page2 = url.searchParams.has("cursor");
                const items = personal
                    ? []
                    : (page2 ? [1, 2] : [0, 1]).map((index) => ({
                          card_uid: `card${index}`,
                          title: `${oldActor} deadline ${index}`,
                          project_uid: url.searchParams.get("project_uid") ?? "left",
                          project_title: `${url.searchParams.get("project_uid") ?? "left"} board`,
                          column_uid: "column",
                          column_name: "Doing",
                          deadline_at: now,
                          work_state: { workflow_stage: "review", material_kind: "work" },
                      }));
                return route.fulfill({
                    json: {
                        items,
                        next_cursor: !personal && !page2 ? "same-deadline-keyset" : null,
                        start: url.searchParams.get("start"),
                        end: url.searchParams.get("end"),
                        scope: url.searchParams.get("scope"),
                    },
                });
            }
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user: user(), bots: [] };
            else if (["/board/left", "/board/right"].includes(path)) json = { project: project(path.split("/")[2]) };
            else if (["/board/left/cards", "/board/right/cards"].includes(path))
                json = { cards: [], columns: [], checklists: [], global_relationships: [] };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project")) json = { projects: [project(), project("right")], columns: [], records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            await route.fulfill({ json });
        }
    );
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("left board", { timeout: 45_000 });
    return {
        reads,
        exports,
        exportTokens,
        setFail: (value: boolean) => {
            fail = value;
        },
        setExportFail: (value: boolean) => {
            exportFail = value;
        },
        setActor: (value: string) => {
            actor = value;
        },
        hold: () => {
            hold = true;
            release = undefined;
        },
        release: () => {
            hold = false;
            release?.();
        },
        held: () => !!release,
    };
}

async function openSchedule(page: Page, mobile = false) {
    if (mobile) {
        await page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "More", exact: true }).click();
        await page.getByRole("menuitem", { name: "Schedule", exact: true }).click();
    } else await page.getByRole("button", { name: "Schedule", exact: true }).click();
    return page.getByRole("navigation", { name: "Schedule", exact: true });
}

test("Schedule lazy native shell scopes, today and timezone month, stable pages, errors, card links and full export", async ({ page }) => {
    const fixture = await scheduleFixture(page);
    expect(fixture.reads).toHaveLength(0);
    expect(fixture.exports).toHaveLength(0);
    const panel = await openSchedule(page);
    await expect(panel.getByText("fixture-user deadline 0", { exact: true })).toBeVisible();
    const [first] = fixture.reads;
    expect(first.searchParams.get("scope")).toBe("board");
    expect(first.searchParams.get("project_uid")).toBe("left");
    const day = await page.evaluate(() => {
        const now = new Date();
        return [
            new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString(),
            new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).toISOString(),
        ];
    });
    expect([first.searchParams.get("start"), first.searchParams.get("end")]).toEqual(day);
    await expect(panel.getByLabel("Start date", { exact: true })).toHaveCount(0);
    await panel.getByRole("button", { name: "Deadlines", exact: true }).click();
    const range = await page.evaluate(() => {
        const now = new Date();
        return [new Date(now.getFullYear(), now.getMonth(), 1).toISOString(), new Date(now.getFullYear(), now.getMonth() + 1, 1).toISOString()];
    });
    await expect.poll(() => [fixture.reads.at(-1)!.searchParams.get("start"), fixture.reads.at(-1)!.searchParams.get("end")]).toEqual(range);
    await panel.getByRole("button", { name: "Load more", exact: true }).click();
    await expect(panel.locator("li")).toHaveCount(3);
    expect(fixture.reads.at(-1)!.searchParams.get("cursor")).toBe("same-deadline-keyset");
    await expect(panel.locator("[data-board-card-work-state]")).toHaveCount(3);
    await page.screenshot({ path: test.info().outputPath("schedule-1440.png") });
    const download = page.waitForEvent("download");
    await panel.getByRole("button", { name: "Download iCal", exact: true }).click();
    expect((await download).suggestedFilename()).toBe("schedule-board.ics");
    expect(fixture.exports[0].searchParams.has("cursor")).toBe(false);
    expect(fixture.exports[0].searchParams.get("start")).toBe(range[0]);
    fixture.setExportFail(true);
    await panel.getByRole("button", { name: "Download iCal", exact: true }).click();
    await expect(panel.getByRole("alert")).toContainText("Calendar export failed");
    await panel.getByRole("button", { name: "Personal schedule", exact: true }).click();
    await expect(panel.getByText("No deadlines in this range.", { exact: true })).toBeVisible();
    expect(fixture.reads.at(-1)!.searchParams.has("project_uid")).toBe(false);
    fixture.setFail(true);
    await panel.getByRole("button", { name: "Board schedule", exact: true }).click();
    await expect(panel.getByRole("alert").filter({ hasText: "Could not load schedule" })).toBeVisible();
    await expect(panel.getByText("No deadlines in this range.", { exact: true })).toHaveCount(0);
    fixture.setFail(false);
    await panel.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(panel.getByText("fixture-user deadline 0", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: /fixture-user deadline 0/ }).click();
    await expect(page.locator("[data-native-location]")).toHaveText("/board/left/card0");
});

test("Schedule fences delayed range, actor and token changes, permissions and export cancellation", async ({ page }) => {
    const fixture = await scheduleFixture(page);
    const panel = await openSchedule(page);
    await expect(panel.locator("li")).toHaveCount(2);
    await panel.getByRole("button", { name: "Deadlines", exact: true }).click();
    await expect(panel.getByLabel("Start date", { exact: true })).toBeVisible();
    const monthStart = await panel.getByLabel("Start date", { exact: true }).inputValue();
    fixture.hold();
    await panel.getByLabel("Start date", { exact: true }).fill(`${monthStart.slice(0, 8)}02`);
    await expect.poll(fixture.held).toBe(true);
    await panel.getByRole("button", { name: "Personal schedule", exact: true }).click();
    fixture.release();
    await expect(panel.getByText("No deadlines in this range.", { exact: true })).toBeVisible();
    await expect(panel.locator("li")).toHaveCount(0);
    await panel.getByRole("button", { name: "Today", exact: true }).click();
    await panel.getByRole("button", { name: "Board schedule", exact: true }).click();
    await expect(panel.locator("li")).toHaveCount(2);
    fixture.hold();
    await panel.getByRole("button", { name: "Load more", exact: true }).click();
    await expect.poll(fixture.held).toBe(true);
    fixture.setActor("second-user");
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ path), import(/* @vite-ignore */ apiPath)]);
        await store.getState().updateToken("schedule-second-actor", api);
    });
    fixture.release();
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("left board");
    if (!(await panel.isVisible())) await openSchedule(page);
    await expect(panel.getByText("second-user deadline 0", { exact: true })).toBeVisible();
    await expect(panel.getByText("fixture-user deadline 0", { exact: true })).toHaveCount(0);
    const before = fixture.reads.length;
    fixture.hold();
    await panel.getByRole("button", { name: "Load more", exact: true }).click();
    await expect.poll(fixture.held).toBe(true);
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ path), import(/* @vite-ignore */ apiPath)]);
        await store.getState().updateToken("schedule-renewed", api);
    });
    fixture.release();
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("left board");
    if (!(await panel.isVisible())) await openSchedule(page);
    await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
    await expect(panel.locator("li")).toHaveCount(2);
    const authorizedDownload = page.waitForEvent("download");
    await panel.getByRole("button", { name: "Download iCal", exact: true }).click();
    await authorizedDownload;
    expect(fixture.exportTokens.at(-1)).toBe("Bearer schedule-renewed");
    fixture.hold();
    const downloads: string[] = [];
    page.on("download", (download) => downloads.push(download.suggestedFilename()));
    await panel.getByRole("button", { name: "Download iCal", exact: true }).click();
    await expect.poll(fixture.held).toBe(true);
    await panel.getByRole("button", { name: "Personal schedule", exact: true }).click();
    fixture.release();
    await expect(panel.getByRole("button", { name: "Download iCal", exact: true })).toBeEnabled();
    await expect(panel.getByText("No deadlines in this range.", { exact: true })).toBeVisible();
    expect(downloads).toEqual([]);
    await page.evaluate(async () => {
        const path = "/src/core/models/index.ts";
        const { Project } = await import(/* @vite-ignore */ path);
        Project.Model.getModel("left").current_auth_role_actions = [];
    });
    await expect(panel.getByRole("button", { name: "Board schedule", exact: true })).toBeDisabled();
});

for (const width of [390])
    test(`Schedule actual mobile drawer bounds, focus, calendar and Escape at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const fixture = await scheduleFixture(page);
        expect(fixture.reads).toHaveLength(0);
        const panel = await openSchedule(page, true);
        await expect(panel.locator("li")).toHaveCount(2);
        const drawer = page.getByRole("complementary", { name: "Schedule", exact: true });
        await expect(drawer).toBeFocused();
        const bounds = await drawer.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.screenshot({ path: test.info().outputPath("schedule-390.png") });
        await panel.getByRole("button", { name: "Deadlines", exact: true }).click();
        await panel.getByRole("button", { name: "Choose month", exact: true }).click();
        await expect(page.getByRole("button", { name: "Done", exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(panel.getByRole("button", { name: "Choose month", exact: true })).toBeFocused();
        await page.keyboard.press("Escape");
        await expect(drawer).toHaveCount(0);
        await expect(
            page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "More", exact: true })
        ).toBeFocused();
    });

test.describe("Schedule browser timezone", () => {
    test.use({ timezoneId: "America/New_York" });
    test("Schedule local date range preserves DST at both boundaries", async ({ page }) => {
        const fixture = await scheduleFixture(page);
        const panel = await openSchedule(page);
        await expect(panel.locator("li")).toHaveCount(2);
        await panel.getByRole("button", { name: "Deadlines", exact: true }).click();
        await panel.getByLabel("Start date", { exact: true }).fill("2026-03-01");
        await panel.getByLabel("End date (exclusive)", { exact: true }).fill("2026-04-01");
        await expect.poll(() => fixture.reads.at(-1)!.searchParams.get("end")).toBe("2026-04-01T04:00:00.000Z");
        expect(fixture.reads.at(-1)!.searchParams.get("start")).toBe("2026-03-01T05:00:00.000Z");
        await panel.getByLabel("End date (exclusive)", { exact: true }).fill("2026-03-01");
        await expect(panel.getByRole("alert")).toContainText("Choose an end date after");
        await expect(panel.locator("li")).toHaveCount(0);
    });
});

test("Schedule closes without requests and reopening follows the current project", async ({ page }) => {
    const fixture = await scheduleFixture(page);
    const panel = await openSchedule(page);
    await expect(panel.locator("li")).toHaveCount(2);
    await page.getByRole("button", { name: "Explorer", exact: true }).click();
    await page.locator("[data-workbench-explorer]").getByRole("button", { name: "right board", exact: true }).first().click();
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("right board");
    expect(fixture.reads.every((url) => url.searchParams.get("project_uid") === "left")).toBe(true);
    await openSchedule(page);
    await expect(panel.getByText("right board · Doing", { exact: true })).toHaveCount(2);
    expect(fixture.reads.at(-1)!.searchParams.get("project_uid")).toBe("right");
});

// Local API fixtures exercise the production card and Plate editors; no provider or canary acceptance is implied.
async function responsiveCommentFixture(page: Page) {
    const now = "2026-10-18T00:00:00Z";
    const member = (uid: string) => ({
        uid,
        type: "user",
        username: uid,
        firstname: uid === "reply-author" ? "Reply" : "Fixture",
        lastname: "Reader",
        created_at: now,
        updated_at: now,
    });
    const comments = Array.from({ length: 26 }, (_, index) => ({
        uid: `responsive-${index}`,
        card_uid: "fixture-card",
        created_at: now,
        updated_at: now,
        content: { content: index === 0 ? "Reply target" : `Thread comment ${index}` },
        is_edited: false,
        reactions: {},
        user: member(index === 0 ? "reply-author" : "sidebar-user-a"),
    }));
    const requests: string[] = [];
    const writes: { method: string; path: string; body: { content: string } }[] = [];
    let realtime = false;
    let submitted: (typeof comments)[number] | undefined;
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const url = new URL(route.request().url());
            const path = url.pathname;
            requests.push(path + url.search);
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            if (route.request().method() === "POST" && path.includes("/attachment")) {
                const requestUID = route
                    .request()
                    .postData()
                    ?.match(/name="request_uid"\r\n\r\n([^\r]+)/)?.[1];
                return route.fulfill({
                    json: {
                        request_uid: requestUID,
                        card_uid: "fixture-card",
                        uid: "responsive-upload",
                        name: "responsive.txt",
                        url: "/file/responsive.txt",
                        order: 0,
                        created_at: now,
                        updated_at: now,
                    },
                });
            }
            if (["POST", "PUT"].includes(route.request().method()) && path.includes("/comment")) {
                const body = route.request().postDataJSON();
                writes.push({ method: route.request().method(), path, body });
                if (route.request().method() === "PUT") comments[1].content = { content: body.content };
                else submitted = { ...comments[1], uid: "submitted", content: { content: body.content } };
                return route.fulfill({ json: { updated_at: now } });
            }
            if (path.endsWith("/timeline")) {
                const currentCard = path.includes("other-card") ? "other-card" : "fixture-card";
                const visible = currentCard === "other-card" ? [] : url.searchParams.has("before") ? comments.slice(20) : comments.slice(0, 20);
                const records = visible.map((comment) => ({
                    kind: "comment",
                    uid: comment.uid,
                    evidence_ref: `comment:${comment.uid}`,
                    created_at: now,
                    data: comment,
                }));
                if (submitted && currentCard === "fixture-card" && !url.searchParams.has("before"))
                    records.unshift({ kind: "comment", uid: submitted.uid, evidence_ref: "comment:submitted", created_at: now, data: submitted });
                if (realtime && currentCard === "fixture-card" && !url.searchParams.has("before"))
                    records.unshift({
                        kind: "comment",
                        uid: "realtime",
                        evidence_ref: "comment:realtime",
                        created_at: now,
                        data: { ...comments[0], uid: "realtime", content: { content: "Realtime arrival" } },
                    });
                return route.fulfill({
                    json: { records, next_cursor: currentCard === "fixture-card" && !url.searchParams.has("before") ? "older" : null },
                });
            }
            if (/\/board\/fixture\/card\/[^/]+$/.test(path)) {
                const uid = path.split("/").at(-1)!;
                return route.fulfill({
                    json: {
                        card: {
                            uid,
                            project_uid: "fixture",
                            project_column_uid: "column",
                            title: uid,
                            created_at: now,
                            updated_at: now,
                            order: 0,
                            description: { content: "Responsive native card" },
                            visibility: "SHARED",
                            count_comment: uid === "fixture-card" ? 26 : 0,
                            member_uids: [],
                            project_members: [member("sidebar-user-a"), member("reply-author")],
                            labels: [],
                            relationships: [],
                            current_auth_role_actions: ["read", "card_update", "card_write"],
                        },
                        attachments: [],
                        checklists: [],
                        global_relationships: [],
                        project_columns: [],
                        project_labels: [],
                        linked_wikis: [],
                        bot_scopes: [],
                    },
                });
            }
            return route.fulfill({ json: { records: [], next_cursor: null, items: [], executions: [] } });
        }
    );
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1&comments=1");
    await expect(page.locator("[data-card-comment-uid='responsive-0']")).toContainText("Reply target", { timeout: 45_000 });
    return {
        requests,
        writes,
        arrive: () => {
            realtime = true;
            return { ...comments[0], uid: "realtime", content: { content: "Realtime arrival" } };
        },
    };
}

async function resizeCommentCard(page: Page, width: number) {
    // Resize the fixture container without an outside click that intentionally closes the editor.
    await page.locator("main > [role=dialog]").evaluate((element, next) => {
        element.style.width = `${next}px`;
    }, width);
    const panel = page.getByRole("separator", { name: /comment/i });
    if (width === 1120) await expect(panel).toBeVisible();
    else await expect(panel).toHaveCount(0);
    await expect(page.locator("[data-card-comment-uid='responsive-0']")).toHaveCount(1);
}

test("responsive native comments preserve reply mention and composer draft across container widths and identity boundaries", async ({ page }) => {
    test.setTimeout(60_000);
    page.setDefaultTimeout(10_000);
    const fixture = await responsiveCommentFixture(page);
    await page.locator("[data-card-comment-uid='responsive-0']").getByRole("button", { name: "Reply", exact: true }).click();
    const editor = page.locator("[data-card-comment-form] [contenteditable='true']");
    await expect(editor).toHaveCount(1);
    await expect(editor).toContainText("Reply Reader");
    await editor.press("End");
    await page.keyboard.type(" Reply draft survives");
    for (const width of [720, 360, 1120]) {
        await resizeCommentCard(page, width);
        await expect(editor).toHaveCount(1);
        await expect(editor).toContainText("Reply Reader");
        await expect(editor).toContainText("Reply draft survives");
    }
    await page.getByText("Switch card", { exact: true }).click();
    await expect(page.getByRole("region", { name: "other-card", exact: true })).toBeVisible();
    await expect(page.locator("[data-card-comment-uid='responsive-0']")).toHaveCount(0);
    await expect(editor).toHaveCount(0);
    await page.getByText("Switch card", { exact: true }).click();
    await expect(page.locator("[data-card-comment-uid='responsive-0']")).toBeVisible();
    await page.getByRole("button", { name: /Add a comment as/ }).click();
    await expect(editor).toContainText("Reply draft survives");
    await page.getByText("Switch account", { exact: true }).click();
    await expect(editor).toHaveCount(0);
    await page.getByRole("button", { name: /Add a comment as/ }).click();
    await expect(editor).not.toContainText("Reply draft survives");
    await editor.click();
    await page.keyboard.type("@reply");
    await page.getByRole("option", { name: /Reply Reader/ }).click();
    await expect(editor).toContainText("Reply Reader");
    await resizeCommentCard(page, 720);
    await expect(editor).toContainText("Reply Reader");
    expect(fixture.writes).toEqual([]);
});

test("responsive native comments preserve edits across layout switches and paginate long threads without duplicates", async ({ page }) => {
    test.setTimeout(60_000);
    page.setDefaultTimeout(10_000);
    const fixture = await responsiveCommentFixture(page);
    const comment = page.locator("[data-card-comment-uid='responsive-1']");
    await comment.getByRole("button", { name: "Edit", exact: true }).click();
    const editor = comment.locator("[contenteditable='true']");
    await editor.press("End");
    await page.keyboard.type(" unsaved edit");
    for (const width of [720, 360, 1120]) {
        await resizeCommentCard(page, width);
        await expect(editor).toContainText("unsaved edit");
        await expect(page.locator("[contenteditable='true']")).toHaveCount(1);
    }
    await comment.getByRole("button", { name: "Save", exact: true }).click();
    await expect(editor).toHaveCount(0);
    await expect(comment).toContainText("unsaved edit");
    expect(fixture.writes).toHaveLength(1);
    expect(fixture.writes[0].method).toBe("PUT");
    expect(fixture.writes[0].body.content).toContain("unsaved edit");
    await page.getByRole("button", { name: "Older history", exact: true }).click();
    await expect(page.locator("[data-card-comment-uid]")).toHaveCount(26);
    for (const width of [720, 360, 1120]) {
        await resizeCommentCard(page, width);
        if (await page.getByRole("button", { name: "Older history", exact: true }).count())
            await page.getByRole("button", { name: "Older history", exact: true }).click();
        await expect(page.locator("[data-card-comment-uid]")).toHaveCount(26);
        expect(
            await page
                .locator("[data-card-comment-uid]")
                .evaluateAll((elements) => new Set(elements.map((element) => element.getAttribute("data-card-comment-uid"))).size)
        ).toBe(26);
    }
});

async function emitResponsiveCommentEvent(page: Page, comment?: unknown) {
    await page.evaluate(
        async (event) => {
            const fixturePath = "/src/components/Editor/collaborative-socket.fixture.ts";
            const { emitCollaborativeFixtureEvent } = await import(/* @vite-ignore */ fixturePath);
            emitCollaborativeFixtureEvent(event);
        },
        comment
            ? {
                  topic: ESocketTopic.Board,
                  topicId: "fixture",
                  event: SocketEvents.SERVER.BOARD.CARD.COMMENT.ADDED.replace("{uid}", "fixture-card"),
                  data: { comment },
              }
            : { event: "open", data: {} }
    );
}

test("responsive native comments retain uploaded attachments and reconcile realtime refresh around an active edit", async ({ page }) => {
    test.setTimeout(90_000);
    page.setDefaultTimeout(10_000);
    const fixture = await responsiveCommentFixture(page);
    await page.getByRole("button", { name: /Add a comment as/ }).click();
    const composer = page.locator("[data-card-comment-form] [contenteditable='true']");
    await expect(composer).toBeVisible();
    await composer.press("End");
    await page.keyboard.type("Attachment draft");
    const fileChooser = page.waitForEvent("filechooser");
    await page.locator("[data-card-comment-form] [role=radio]:has(svg.lucide-file-up)").first().click();
    await (await fileChooser).setFiles({ name: "responsive.txt", mimeType: "text/plain", buffer: Buffer.from("native attachment fixture") });
    const attachment = page.locator("[data-card-comment-form] .slate-file");
    await expect(attachment).toContainText("responsive.txt");
    for (const width of [720, 360, 1120]) {
        await resizeCommentCard(page, width);
        await expect(composer).toHaveCount(1);
        await expect(composer).toContainText("Attachment draft");
        await expect(attachment).toContainText("responsive.txt");
    }
    await page.locator("[data-card-comment-form]").getByRole("button", { name: "Save", exact: true }).click();
    await expect(composer).toHaveCount(0);
    expect(fixture.writes).toHaveLength(1);
    expect(fixture.writes[0].method).toBe("POST");
    expect(fixture.writes[0].body.content).toContain("responsive.txt");
    expect(fixture.writes[0].body.content).toContain("/file/responsive.txt");
    await expect(page.locator("[data-card-comment-uid='submitted'] .slate-file")).toContainText("responsive.txt");
    await page.reload();
    await expect(page.locator("[data-card-comment-uid='submitted'] .slate-file")).toContainText("responsive.txt");
    const comment = page.locator("[data-card-comment-uid='responsive-1']");
    await comment.getByRole("button", { name: "Edit", exact: true }).click();
    const editor = comment.locator("[contenteditable='true']");
    await editor.press("End");
    await page.keyboard.type(" guarded draft");
    const reads = fixture.requests.filter((path) => path.includes("/timeline")).length;
    const arrival = fixture.arrive();
    await emitResponsiveCommentEvent(page, arrival);
    // The production listener debounces by 100ms; allow it to attempt refresh while edit remains open.
    await page.waitForTimeout(250);
    expect(fixture.requests.filter((path) => path.includes("/timeline"))).toHaveLength(reads);
    await expect(editor).toContainText("guarded draft");
    await comment.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.locator("[data-card-comment-uid='realtime']")).toContainText("Realtime arrival");
    await expect(page.locator("[data-card-comment-uid='realtime']")).toHaveCount(1);
    await emitResponsiveCommentEvent(page);
    await expect.poll(() => fixture.requests.filter((path) => path.includes("/timeline")).length).toBe(reads + 2);
    await expect(page.locator("[data-card-comment-uid='realtime']")).toHaveCount(1);
    expect(fixture.writes).toHaveLength(1);
});

test("sidebar transitions respect reduced motion and keep ordinary dragging immediate", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html");
    const sidebar = page.locator("[data-workbench-sidebar]");
    const transitions = () =>
        sidebar.evaluate((element) => {
            const style = getComputedStyle(element);
            return { property: style.transitionProperty, duration: style.transitionDuration };
        });
    await expect.poll(transitions).toEqual({ property: "all", duration: "0.2s" });
    const handle = await sidebar.locator(".resizer").boundingBox();
    await page.mouse.move(handle!.x + 1, handle!.y + 100);
    await page.mouse.down();
    await expect(sidebar).toHaveAttribute("data-resizing", "true");
    await expect.poll(async () => (await transitions()).property).toBe("none");
    await page.mouse.up();
    await expect.poll(transitions).toEqual({ property: "all", duration: "0.2s" });
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    await expect.poll(async () => (await transitions()).property).toBe("none");
    await sidebar.getByRole("button", { name: /Collapse|접기/ }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect.poll(async () => (await transitions()).property).toBe("none");
    await sidebar.getByRole("button", { name: /Expand|펼치기/ }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect.poll(async () => (await transitions()).property).toBe("none");
});

async function nativeWorkbenchFixture(page: Page, holdBoard = false) {
    const now = new Date().toISOString();
    const user = {
        uid: "measure-user",
        created_at: now,
        updated_at: now,
        type: "user",
        firstname: "Fixture",
        lastname: "Reader",
        email: "fixture@example.com",
        username: "measure",
        api_key_role_actions: ["*"],
        setting_role_actions: ["*"],
        mcp_role_actions: ["*"],
        user_groups: [],
        subemails: [],
        preferred_lang: "en",
        notification_unsubs: {},
        industry: "",
        purpose: "",
    };
    const projects = ["left0000001", "right000001"].map((uid) => ({
        uid,
        title: `${uid} board`,
        created_at: now,
        updated_at: now,
        owner_uid: user.uid,
        project_type: "default",
        archive_visible_days: 30,
        all_members: [user],
        invited_member_uids: [],
        starred: false,
        internal_bots: [],
        internal_bot_settings: {},
        current_auth_role_actions: ["*"],
        labels: [],
        description: "",
        last_viewed_at: now,
        view_count: 0,
    }));
    const card = (uid: string) => ({
        uid: uid,
        project_uid: uid,
        project_column_uid: `${uid}-column`,
        project_column_name: `${uid} column`,
        title: `${uid} measured card`,
        created_at: now,
        updated_at: now,
        order: 0,
        count_comment: 0,
        member_uids: [],
        current_auth_role_actions: ["*"],
        project_members: [],
        labels: [],
        relationships: [],
        last_change_seq: 1,
        has_unread_change: false,
        has_description: true,
        description: { content: "Measured native card body" },
        visibility: "SHARED",
    });
    const column = (uid: string) => ({
        uid: `${uid}-column`,
        project_uid: uid,
        name: `${uid} column`,
        order: 0,
        created_at: now,
        updated_at: now,
        color: "#444444",
    });
    const http: { method: string; path: string; startMs: number; endMs?: number }[] = [];
    const errors: string[] = [];
    const socketSenders = new Map<string, (message: string) => void>();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.routeWebSocket(/.*/, (ws) =>
        ws.onMessage((raw) => {
            if (!String(raw).startsWith("{")) return;
            const m = JSON.parse(String(raw));
            if (m.event === "subscribe") socketSenders.set(ws.url(), (message) => ws.send(message));
            if (["subscribe", "unsubscribe"].includes(m.event))
                ws.send(JSON.stringify({ ...m, event: m.event === "subscribe" ? "subscribed" : "unsubscribed" }));
        })
    );
    let releaseBoard: (() => void) | undefined;
    let holdIdentity = false;
    let releaseIdentity: (() => void) | undefined;
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const request = route.request();
            const path = new URL(request.url()).pathname;
            const row = { method: request.method(), path, startMs: Date.now(), endMs: undefined as number | undefined };
            http.push(row);
            if (row.method === "OPTIONS") {
                await route.fulfill({ status: 204 });
                row.endMs = Date.now();
                return;
            }
            const uid = path.split("/")[2];
            let json: unknown = { records: [], next_cursor: null, items: [], executions: [], decisions: [], comments: [] };
            if (new URL(request.url()).searchParams.has("location_only")) json = { canonical_project_uid: uid, card_uid: path.split("/")[4] };
            else if (path.endsWith("/seen")) json = { card_uid: path.split("/")[4], seen_change_seq: 1 };
            else if (path.startsWith("/auth/")) {
                if (holdIdentity && path === "/auth/me") await new Promise<void>((resolve) => (releaseIdentity = resolve));
                json = { access_token: "fixture-measurement-session", user, bots: [] };
            } else if (path === `/board/${uid}`) {
                if (holdBoard)
                    await new Promise<void>((resolve) => {
                        releaseBoard = resolve;
                    });
                json = { project: projects.find((p) => p.uid === uid), project_bot_scopes: [], project_bot_schedules: [] };
            } else if (path === `/board/${uid}/cards`)
                json = { cards: [card(uid)], columns: [column(uid)], checklists: [], global_relationships: [] };
            else if (path === `/board/${uid}/card/${uid}`)
                json = {
                    card: card(uid),
                    project: projects.find((p) => p.uid === uid),
                    members: [],
                    checklists: [],
                    comments: [],
                    activities: [],
                    files: [],
                    attachments: [],
                    linked_wikis: [],
                    global_relationships: [],
                    project_columns: [],
                    project_labels: [],
                    bot_scopes: [],
                };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path === "/account/project-collections") json = { revision: 0, collections: [] };
            else if (path.includes("/search")) json = { items: [], next_cursor: null };
            else if (path.includes("project")) json = { projects, columns: projects.map((p) => column(p.uid)), records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            else if (path.includes("timeline")) json = { items: [], next_cursor: null };
            await route.fulfill({ json });
            row.endMs = Date.now();
        }
    );

    return {
        http,
        errors,
        boardPending: () => !!releaseBoard,
        releaseBoard: () => releaseBoard!(),
        holdIdentity: () => (holdIdentity = true),
        identityPending: () => !!releaseIdentity,
        releaseIdentity: () => releaseIdentity!(),
        revoke: async (projectUID: string) => {
            expect(typeof SocketEvents.SERVER.SUBSCRIPTION_REVOKED).toBe("string");
            const url = await page.evaluate(async () => {
                const path = "/src/core/stores/SocketStore.ts";
                const { default: store } = await import(path);
                return store.getState().getSocket()?.url;
            });
            const sendSocket = socketSenders.get(url!);
            expect(sendSocket).toBeDefined();
            sendSocket!(
                JSON.stringify({ topic: ESocketTopic.Board, topic_id: projectUID, event: SocketEvents.SERVER.SUBSCRIPTION_REVOKED, data: {} })
            );
        },
    };
}

test("native dashboard-to-board pending split registration preserves physical chrome and blocks stale controls", async ({ page }) => {
    const { http, errors, boardPending, releaseBoard } = await nativeWorkbenchFixture(page, true);
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?measure-native=1");
    const explorer = page.locator("[data-workbench-explorer]");
    await expect(explorer.getByRole("button", { name: "left0000001 board", exact: true }).first()).toBeVisible({ timeout: 45_000 });
    const header = await page.locator("header").elementHandle();
    const rail = await page.getByRole("navigation", { name: "Workspace", exact: true }).elementHandle();
    const sidebar = await page.locator("[data-workbench-sidebar]").elementHandle();
    const notificationReads = () => http.filter((request) => request.path === "/notifications").length;
    const before = notificationReads();
    await explorer.getByRole("button", { name: "left0000001 board", exact: true }).first().click();
    await expect.poll(boardPending).toBe(true);
    // The focused board is still pending; old controls must remain mounted and inert.
    await expect(page.locator("[inert][aria-busy=true]")).toHaveCount(1);
    for (const node of [header, rail, sidebar]) expect(await node!.evaluate((element) => element.isConnected)).toBe(true);
    releaseBoard();
    await expect(page.getByText("left0000001 measured card", { exact: true }).first()).toBeVisible();
    await expect(page.locator("[inert]")).toHaveCount(0);
    for (const node of [header, rail, sidebar]) expect(await node!.evaluate((element) => element.isConnected)).toBe(true);
    expect(notificationReads()).toBe(before);
    expect(errors).toEqual([]);
});

test("native card navigation retains board reads while project, session and revocation stay fresh", async ({ page }) => {
    const { http, errors, revoke, holdIdentity, identityPending, releaseIdentity } = await nativeWorkbenchFixture(page);
    const reads = (uid: string, from = 0) =>
        http
            .slice(from)
            .filter(
                ({ method, path }) => method === "GET" && [`/board/${uid}`, `/board/${uid}/cards`, `/metadata/project/${uid}/cards`].includes(path)
            )
            .map(({ path }) => path);
    const freshReads = (uid: string) => [`/board/${uid}`, `/board/${uid}/cards`, `/metadata/project/${uid}/cards`];
    const openBoard = async (uid: string) => {
        await page
            .locator("[data-workbench-explorer]")
            .getByRole("button", { name: `${uid} board`, exact: true })
            .first()
            .click();
        await expect(page.getByText(`${uid} measured card`, { exact: true }).first()).toBeVisible();
    };
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?measure-native=1");
    await openBoard("left0000001");
    await expect.poll(() => reads("left0000001")).toEqual(freshReads("left0000001"));
    const column = await page.getByText("left0000001 column", { exact: true }).first().elementHandle();
    let from = http.length;
    await page.getByText("left0000001 measured card", { exact: true }).first().click();
    await expect(page.locator("[data-card-surface]").last()).toContainText("Measured native card body");
    await expect.poll(() => http.slice(from).some(({ method, path }) => method === "POST" && path.endsWith("/seen"))).toBe(true);
    // Opening an unchanged card still records its seen cursor, without replacing
    // the authorized board snapshot or the mounted realtime owners.
    expect(reads("left0000001", from)).toEqual([]);
    expect(await column!.evaluate((element) => element.isConnected)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-card-surface]")).toHaveCount(0);
    expect(reads("left0000001", from)).toEqual([]);
    expect(await column!.evaluate((element) => element.isConnected)).toBe(true);

    from = http.length;
    await openBoard("right000001");
    await expect.poll(() => reads("right000001", from)).toEqual(freshReads("right000001"));
    from = http.length;
    holdIdentity();
    await page.evaluate(async () => {
        const storePath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const { default: store } = await import(storePath);
        const { api } = await import(apiPath);
        void store.getState().updateToken("fixture-next-session", api);
    });
    await expect.poll(identityPending).toBe(true);
    await expect
        .poll(() =>
            page.evaluate(async () => {
                const path = "/src/core/stores/SocketStore.ts";
                const { default: sockets } = await import(path);
                return sockets.getState().getSocket() === null;
            })
        )
        .toBe(true);
    releaseIdentity();
    await expect.poll(() => reads("right000001", from)).toEqual(freshReads("right000001"));
    await expect(page.getByText("right000001 measured card", { exact: true }).first()).toBeVisible();
    await expect
        .poll(() =>
            page.evaluate(async () => {
                const socketPath = "/src/core/stores/SocketStore.ts";
                const authPath = "/src/core/stores/AuthStore.ts";
                const { default: sockets } = await import(socketPath);
                const { default: auth } = await import(authPath);
                const socket = sockets.getState().getSocket();
                return {
                    open: socket?.readyState === WebSocket.OPEN,
                    currentToken: !!socket && new URL(socket.url).searchParams.get("authorization") === auth.getState().getToken(),
                    boardSubscribed: sockets.getState().getStore().subscribedTopics.board?.includes("right000001") === true,
                };
            })
        )
        .toEqual({ open: true, currentToken: true, boardSubscribed: true });
    await revoke("right000001");
    await expect(page).toHaveURL(/dashboard\/projects\/all/);
    await expect(page.getByText("right000001 column", { exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
});
