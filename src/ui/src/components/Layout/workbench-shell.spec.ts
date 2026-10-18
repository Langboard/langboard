import { hasPanelAccess } from "../../core/apps/PanelAccess";
import type { CatalogApp } from "../../controllers/api/board/useBoardAppCatalog";
import type { Page, Route } from "@playwright/test";
import { expect, test } from "@playwright/test";

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
    await expect.poll(() => sidebar.evaluate((element) => element.getBoundingClientRect().width)).toBe(52);
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
    await expect(markers).toHaveCount(60);
    const first = markers.first();
    const bounds = await viewport.boundingBox();
    const initial = await first.boundingBox();
    expect(initial!.y).toBeGreaterThanOrEqual(bounds!.y);
    expect(initial!.y + initial!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
    await viewport.focus();
    await viewport.press("PageDown");
    await viewport.press("PageDown");
    await expect
        .poll(async () => page.locator("button[aria-current='location']").getAttribute("aria-label"))
        .not.toBe(await first.getAttribute("aria-label"));
    await markers.nth(35).click();
    await expect
        .poll(() => page.locator("button[aria-current='location']").getAttribute("aria-label"))
        .toBe(await markers.nth(35).getAttribute("aria-label"));
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
    await expect(markers).toHaveCount(60);
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
            expect(url.searchParams.get("project_uid")).toBe("2RRwwB5JNm0");
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
        await expect(page.getByRole("link", { name: "Configure on this board" })).toHaveAttribute("href", "/board/2RRwwB5JNm0/settings/apps");
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
            await page.getByRole("button", { name: "Add a card", exact: true }).click();
            const title = "고객 요청 초안 — preserve on failure";
            const input = page.locator("textarea");
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

const lensCard = (uid = "fixture-card") => ({
    uid,
    project_uid: "fixture",
    project_column_uid: "column",
    title: uid === "fixture-card" ? "Lens card" : "Other lens card",
    order: 0,
    created_at: "2026-10-18T00:00:00Z",
    updated_at: "2026-10-18T00:00:00Z",
    source_type: "task",
    visibility: "SHARED",
    project_column_name: "Doing",
    member_uids: [],
    project_members: [],
    labels: [],
    relationships: [],
    current_auth_role_actions: ["read"],
    count_comment: 0,
    can_delete: false,
    can_change_visibility: false,
    is_check_card: false,
    completed: false,
    work_state: {
        version: 1,
        workflow_stage: "active",
        verification_state: "stale",
        verification_source_change_seq: 8,
        verification: {
            uid: "verification",
            source_change_seq: 7,
            decision: "partial",
            recorded_at: "2026-10-18T00:00:00Z",
            recorded_by_user_uid: null,
            recorded_by_bot_uid: null,
            evidence: [{ reference: "source-test.log", source_revision: "abc123", environment: "local-only", checkitem_uid: "acceptance" }],
            required_checkitem_uids: ["acceptance", "missing"],
        },
        execution_generation: 7,
        execution_state: null,
        blocker_state: null,
        material_kind: "work",
        lifecycle: "active",
        active_queue_eligible: null,
        checklist_progress: { total: 2, completed: 1 },
        reasons: [{ code: "verification_stale", message: "Raw private reason must not be shown", source_ref: "secret" }],
        state_inconsistency: [],
    },
});

for (const width of [1440, 320, 390]) {
    for (const [lang, labels] of Object.entries({
        "en-US": ["Execution", "Review", "History", "Unknown", "Stale"],
        "ko-KR": ["실행", "검토", "기록", "알 수 없음", "오래됨"],
        "ja-JP": ["実行", "レビュー", "履歴", "不明", "古い検証"],
        "zh-CN": ["执行", "审核", "历史", "未知", "已过期"],
    })) {
        test(`native card lenses lazily read sections and support keyboard at ${width}px ${lang}`, async ({ page }) => {
            const errors: string[] = [];
            page.on("pageerror", (error) => errors.push(error.message));
            await page.setViewportSize({ width, height: 844 });
            const requests: string[][] = [];
            const extra: string[] = [];
            await page.route(/\/board\/fixture\/card\/[^/?]+\?sections=/, async (route) => {
                const sections = new URL(route.request().url()).searchParams.get("sections")!.split(",");
                requests.push(sections);
                const result: Record<string, unknown> = { card: lensCard(), whispers_allowed: false };
                for (const section of sections) if (section !== "description") result[section] = [];
                if (sections.includes("execution_receipts"))
                    result.execution_receipts = [6, 7].map((generation) => ({
                        generation,
                        created_at: "2026-10-18T00:00:00Z",
                        checklist_projection: [],
                        receipt: { status: "completed", summary: `Generation ${generation} evidence`, artifacts: [], checklist_evidence: [] },
                    }));
                if (sections.includes("description"))
                    (result.card as Record<string, unknown>).description = { content: "History description", type: "text" };
                await route.fulfill({ json: result });
            });
            await page.route("**/board/fixture/card/*/apps/**", (route) => {
                extra.push(route.request().url());
                return route.fulfill({ json: { items: [], executions: [], next_cursor: null } });
            });
            await page.route("**/board/fixture/card/*/seen", (route) => route.fulfill({ json: { card_uid: "fixture-card", seen_change_seq: 0 } }));
            await page.route(/^http:\/\/localhost:5381\/activity\//, (route) => route.fulfill({ json: { records: [], next_cursor: null } }));
            await page.goto(`/src/components/Layout/workbench-shell.fixture.html?lenses=1&lang=${lang}`);
            await expect.poll(() => requests.length, { timeout: 20000 }).toBeGreaterThan(0);
            const tabs = page.getByRole("tablist");
            await expect(tabs.getByRole("tab", { name: labels[0], exact: true })).toHaveAttribute("aria-selected", "true", { timeout: 20000 });
            await expect(page.getByRole("tabpanel")).toContainText(labels[3]);
            expect(requests).toHaveLength(1);
            expect(requests[0]).not.toContain("description");
            expect(requests[0]).not.toContain("attachments");
            expect(extra).toHaveLength(0);
            await expect(page.getByRole("tabpanel")).toContainText("Generation 7 evidence");
            await expect(page.getByRole("tabpanel")).not.toContainText("Generation 6 evidence");
            if (lang === "en-US") {
                await page.waitForTimeout(400);
                await page.screenshot({ path: `../../.verification/workbench-lenses-20261019/controlled-${width}-execution.png` });
            }
            await expect(page.getByText("Raw private reason must not be shown")).toHaveCount(0);
            await tabs.getByRole("tab", { name: labels[0], exact: true }).focus();
            await page.keyboard.press("ArrowRight");
            await expect(tabs.getByRole("tab", { name: labels[1], exact: true })).toBeFocused();
            await expect(page.getByRole("tabpanel")).toContainText(labels[4]);
            await expect(page.getByRole("tabpanel")).toContainText("abc123");
            await expect(page.getByRole("tabpanel")).toContainText("local-only");
            if (lang === "en-US") {
                await page.waitForTimeout(400);
                await page.screenshot({ path: `../../.verification/workbench-lenses-20261019/controlled-${width}-review.png` });
            }
            await page.keyboard.press("End");
            await expect(tabs.getByRole("tab", { name: labels[2], exact: true })).toHaveAttribute("aria-selected", "true");
            await expect.poll(() => requests.some((sections) => sections.includes("description"))).toBe(true);
            await expect(page.getByRole("tabpanel")).toContainText("History description");
            await expect(page.getByRole("tabpanel")).toContainText("Generation 6 evidence");
            if (lang === "en-US") {
                await page.waitForTimeout(400);
                await page.screenshot({ path: `../../.verification/workbench-lenses-20261019/controlled-${width}-history.png` });
            }
            const identity = await page.locator("[data-card-surface]").getAttribute("data-card-privacy");
            await tabs.getByRole("tab", { name: labels[0], exact: true }).click();
            await expect(tabs.getByRole("tab", { name: labels[0], exact: true })).toHaveAttribute("aria-selected", "true");
            expect(await page.locator("[data-card-surface]").getAttribute("data-card-privacy")).toBe(identity);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await expect(page.locator("iframe")).toHaveCount(0);
            expect(errors).toEqual([]);
        });
    }
}

async function prepareDelayedLens(page: Page, { editable = false, unread = false, failSave = false, unknown = false, deadline = false } = {}) {
    const reads: string[] = [];
    const writes: unknown[] = [];
    let releaseHistory: (() => void) | undefined;
    let delayed = true;
    let historyReleased = false;
    await page.route("http://localhost:5381/**", async (route) => {
        const url = new URL(route.request().url());
        const path = url.pathname;
        reads.push(path);
        if (/\/card\/[^/]+$/.test(path) && route.request().method() === "GET") {
            const sections = url.searchParams.get("sections")!.split(",");
            if (sections.includes("description") && delayed) {
                delayed = false;
                if (!historyReleased)
                    await new Promise<void>((resolve) => {
                        releaseHistory = resolve;
                    });
            }
            const uid = path.split("/").at(-1)!;
            const card = {
                ...lensCard(uid),
                work_state: unknown ? undefined : lensCard(uid).work_state,
                deadline_at: deadline ? "2030-06-15T12:00:00Z" : null,
                current_auth_role_actions: editable ? ["*"] : ["read"],
                count_comment: 2,
                last_change_seq: 13,
                has_unread_change: unread,
                last_change_target_type: "description",
                last_change_target_uid: null,
            };
            const data: Record<string, unknown> = { card, whispers_allowed: false };
            for (const section of sections) if (section !== "description") data[section] = [];
            if (sections.includes("description")) (data.card as Record<string, unknown>).description = { content: "Delayed history description" };
            await route.fulfill({ json: data }).catch(() => {});
        } else if (path.endsWith("/seen")) {
            await route.fulfill({ json: { card_uid: "fixture-card", seen_change_seq: 13 } });
        } else if (path.endsWith("/details") && route.request().method() === "PUT") {
            writes.push(route.request().postDataJSON());
            await route.fulfill({ status: failSave ? 500 : 200, json: {} });
        } else if (path.endsWith("/comments")) {
            await route.fulfill({ json: { comments: [] } });
        } else await route.fulfill({ json: { items: [], records: [], next_cursor: null } });
    });
    return {
        reads,
        writes,
        release: () => {
            historyReleased = true;
            releaseHistory?.();
        },
    };
}

for (const width of [1440, 390])
    test(`comment controls and delayed outline navigation remain usable at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const fixture = await prepareDelayedLens(page);
        await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
        await expect(page.getByRole("tab", { name: "Execution", exact: true })).toHaveAttribute("aria-selected", "true");
        expect(fixture.reads.filter((path) => path.endsWith("/comments"))).toEqual([]);
        await page.getByRole("button", { name: "Comments", exact: true }).click();
        await expect(page.getByRole("tab", { name: "History", exact: true })).toHaveAttribute("aria-selected", "true");
        if (width === 1440) await expect.poll(() => fixture.reads.filter((path) => path.endsWith("/comments")).length).toBe(1);
        await page.evaluate(() =>
            window.dispatchEvent(
                new CustomEvent("langboard:open-card-outline-section", { detail: { cardUID: "fixture-card", section: "description" } })
            )
        );
        fixture.release();
        await expect(page.locator("[data-card-outline-section=description]")).toContainText("Delayed history description");
        await expect(page.locator("[data-card-outline-section=description]")).toBeFocused();
        await expect.poll(() => fixture.reads.filter((path) => path.endsWith("/comments")).length).toBeGreaterThanOrEqual(1);
        if (width === 1440) await expect(page.getByRole("separator", { name: "Comments panel width" })).toBeVisible();
    });

test("unread navigation waits for its history section and preserves native highlighting", async ({ page }) => {
    const fixture = await prepareDelayedLens(page, { unread: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await expect(page.getByRole("tab", { name: "History", exact: true })).toHaveAttribute("aria-selected", "true");
    fixture.release();
    await expect(page.locator("[data-card-outline-section=description]")).toBeFocused();
    await expect(page.locator("[data-card-outline-section=description]")).toHaveClass(/unread-target-highlight/);
    expect(fixture.reads.filter((path) => path.endsWith("/seen"))).toHaveLength(1);
});

test("editing opens the same card history and retains title drafts through discard and flip gating", async ({ page }) => {
    const fixture = await prepareDelayedLens(page, { editable: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("tab", { name: "History", exact: true })).toHaveAttribute("aria-selected", "true");
    fixture.release();
    await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await page.getByRole("heading", { name: "Lens card", exact: true }).click();
    const title = page.locator("textarea").filter({ visible: true }).first();
    await expect(title).toBeVisible();
    await title.fill("Retained title draft");
    await expect(title).toHaveValue("Retained title draft");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Lens card", exact: true })).toBeVisible();
    expect(fixture.writes).toEqual([]);
    await expect(page.getByRole("tab", { name: "History", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Flip card", exact: true }).click();
    await expect(page.getByTestId("flipped-card")).toHaveText("fixture-card");
});

for (const failSave of [false, true])
    test(`native card save ${failSave ? "retains the failed draft" : "submits the edited title"}`, async ({ page }) => {
        const fixture = await prepareDelayedLens(page, { editable: true, failSave });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await expect(page.getByRole("tab", { name: "History", exact: true })).toHaveAttribute("aria-selected", "true");
        fixture.release();
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
        await page.getByRole("heading", { name: "Lens card", exact: true }).click();
        const title = page.locator("textarea").filter({ visible: true }).first();
        await expect(title).toBeEnabled();
        await title.fill("Saved title draft");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => fixture.writes.length).toBe(1);
        expect(fixture.writes[0]).toMatchObject({ title: "Saved title draft" });
        if (failSave) {
            await expect(title).toHaveValue("Saved title draft");
            await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeDisabled();
            await page.getByRole("button", { name: "Cancel", exact: true }).click();
        } else await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeEnabled();
    });

test("card and account switches reset lens selection without displaying the previous history", async ({ page }) => {
    const fixture = await prepareDelayedLens(page);
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await page.getByRole("tab", { name: "History", exact: true }).click();
    fixture.release();
    await expect(page.getByRole("tabpanel")).toContainText("Delayed history description");
    await page.getByRole("tab", { name: "Review", exact: true }).click();
    await page.getByRole("tab", { name: "History", exact: true }).click();
    await expect(page.getByRole("tabpanel")).toContainText("Delayed history description");
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Other lens card", exact: true })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Execution", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel")).not.toContainText("Delayed history description");
    await page.getByRole("tab", { name: "Review", exact: true }).click();
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByRole("tab", { name: "Execution", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("unknown verification and agent lifecycle never invent approval actions", async ({ page }) => {
    await prepareDelayedLens(page, { unknown: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await expect(page.getByRole("tabpanel")).toContainText("Agent lifecycle: Unknown");
    await page.getByRole("tab", { name: "Review", exact: true }).click();
    await expect(page.getByRole("tabpanel")).toContainText("Verification: Unknown");
    await expect(page.getByRole("button", { name: /Approve/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
});

test("deadline draft removal is discarded through the original card editor", async ({ page }) => {
    const fixture = await prepareDelayedLens(page, { editable: true, deadline: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("tab", { name: "History", exact: true })).toHaveAttribute("aria-selected", "true");
    fixture.release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await page.getByText(/2030/).first().click();
    await page.getByRole("button", { name: "Remove deadline", exact: true }).click();
    await expect(page.getByRole("button", { name: "Remove deadline", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByText(/2030/).first()).toBeVisible();
    expect(fixture.writes).toEqual([]);
});
