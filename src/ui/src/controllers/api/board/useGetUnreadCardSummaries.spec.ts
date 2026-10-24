import { expect, test, type Page, type Route } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/board/unread-summary.fixture.html";
const sidebar = (page: Page) => page.getByRole("region", { name: "Changes" });
const row = (board: string, uid: string, status = "exact") => ({
    uid,
    project_uid: board,
    title: `${board} ${uid}`,
    has_unread_change: true,
    unread_change_count: status === "exact" ? 3 : null,
    unread_change_count_status: status,
    latest_change: { seq: 10, target_type: "comment", target_uid: "latest-comment", at: "2026-10-23T01:00:00Z" },
});
async function setup(page: Page, respond?: (route: Route, board: string, cursor: string | null) => Promise<void>) {
    const reads: string[] = [];
    const whole: string[] = [];
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/board/**", async (route) => {
        const url = new URL(route.request().url());
        if (url.port !== "5381") return route.continue();
        if (!url.pathname.endsWith("/unread-card-summaries")) {
            if (url.pathname.endsWith("/cards")) whole.push(url.pathname);
            return route.fulfill({ json: {} });
        }
        const board = url.pathname.split("/").at(-2)!;
        reads.push(`${board}:${url.searchParams.get("cursor") ?? "first"}`);
        expect(url.searchParams.get("limit")).toBe("50");
        if (respond) return respond(route, board, url.searchParams.get("cursor"));
        return route.fulfill({ json: { items: [row(board, "card")], next_cursor: null, limit: 50 } });
    });
    await page.goto(fixture);
    return { reads, whole, errors };
}
async function emit(page: Page, topic: string, id: string, event = "board:card:details:changed:card", data = { change_count_changed: true }) {
    await page.evaluate(
        ({ topic, id, event, data }) => {
            const native = window as unknown as {
                emitNativeSummaryEvent: (topic: string, id: string, event: string, data: unknown) => Promise<void>;
            };
            return native.emitNativeSummaryEvent(topic, id, event, data);
        },
        { topic, id, event, data }
    );
}
async function control(page: Page, name: string, value?: string) {
    await page.evaluate(
        ({ name, value }) => {
            const fixture = (window as unknown as { unreadFixture: Record<string, (value?: string) => unknown> }).unreadFixture;
            return fixture[name](value);
        },
        { name, value }
    );
}

test("real sidebar uses bounded summaries, exact/manual/unavailable rows, paging and navigation without card hydration", async ({ page }) => {
    const state = await setup(page, async (route, board, cursor) =>
        route.fulfill({
            json: {
                items: cursor
                    ? [row(board, "next")]
                    : [row(board, "card"), row(board, "manual", "manual_unread"), row(board, "unknown", "unknown_history")],
                next_cursor: cursor ? null : "opaque",
                limit: 50,
            },
        })
    );
    await expect(sidebar(page)).toContainText("left card");
    await expect(sidebar(page)).toContainText("3 unread changes");
    await expect(sidebar(page)).toContainText("Marked unread");
    await expect(sidebar(page)).toContainText("Change count unavailable");
    await expect.poll(() => page.evaluate(() => (window as unknown as { unreadFixture: { models: () => number } }).unreadFixture.models())).toBe(0);
    await page.getByRole("button", { name: "More", exact: true }).click();
    await expect(sidebar(page)).toContainText("left next");
    await expect(sidebar(page)).not.toContainText("left card");
    await sidebar(page)
        .getByRole("button", { name: /left next/ })
        .click();
    await expect(page.locator("[data-location]")).toHaveText("/board/left/next");
    expect(state.reads).toEqual(["left:first", "left:opaque"]);
    expect(state.whole).toEqual([]);
    expect(state.errors).toEqual([]);
});

test("native public/private receipts, query invalidation, refresh and reconnect remain live without irrelevant-event scans", async ({ page }) => {
    let revision = 0;
    const state = await setup(page, async (route, board) =>
        route.fulfill({ json: { items: [row(board, `revision-${++revision}`)], next_cursor: null, limit: 50 } })
    );
    await expect(sidebar(page)).toContainText("revision-1");
    await emit(page, "board", "left");
    await expect(sidebar(page)).toContainText("revision-2");
    await emit(page, "user_private", "actor-a", "board:card:details:changed:private", { read_state_changed: true } as never);
    await expect(sidebar(page)).toContainText("revision-3");
    const before = state.reads.length;
    await emit(page, "board", "right");
    await emit(page, "user_private", "actor-b");
    await emit(page, "board", "left", "board:card:editor:users:card");
    await page.waitForTimeout(100);
    expect(state.reads.length).toBe(before);
    await control(page, "invalidateBoard");
    await expect(sidebar(page)).toContainText("revision-4");
    await control(page, "invalidateReadState");
    // No cached read-state query exists, so the native mutation's board invalidation is the meaningful signal.
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(sidebar(page)).toContainText("revision-5");
    await control(page, "reconnect");
    await expect(sidebar(page)).toContainText("revision-6");
    expect(state.whole).toEqual([]);
    expect(state.errors).toEqual([]);
});

test("board/actor changes reject delayed old rows, failures hide cached authority and retry recovers", async ({ page }) => {
    let hold: Route | undefined;
    let fail = false;
    let requests = 0;
    const state = await setup(page, async (route, board) => {
        if (++requests === 1) {
            hold = route;
            return;
        }
        return route.fulfill(
            fail ? { status: 403, json: {} } : { json: { items: [row(board, `request-${requests}`)], next_cursor: null, limit: 50 } }
        );
    });
    await expect.poll(() => !!hold).toBe(true);
    await control(page, "selectActor", "actor-b");
    await expect(sidebar(page)).toContainText("request-2");
    await hold!.fulfill({ json: { items: [row("left", "OLD-ACTOR")], next_cursor: null, limit: 50 } }).catch(() => {});
    await expect(sidebar(page)).not.toContainText("OLD-ACTOR");
    await page.getByRole("button", { name: "Switch board" }).click();
    await expect(sidebar(page)).toContainText("right request-3");
    fail = true;
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(sidebar(page).getByRole("alert")).toBeVisible();
    await expect(sidebar(page)).not.toContainText("request-3");
    fail = false;
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(sidebar(page)).toContainText("right request-5");
    expect(state.whole).toEqual([]);
    expect(state.errors).toEqual([]);
});

test("private role revocation refreshes only the affected actor/project and retires cached rows", async ({ page }) => {
    let revoked = false;
    const state = await setup(page, async (route, board) =>
        route.fulfill(revoked ? { status: 403, json: {} } : { json: { items: [row(board, "card")], next_cursor: null, limit: 50 } })
    );
    await expect(sidebar(page)).toContainText("left card");
    const before = state.reads.length;
    await emit(page, "user_private", "actor-a", "user:project-roles:updated", { project_uid: "right", roles: [] } as never);
    await emit(page, "user_private", "actor-b", "user:project-roles:updated", { project_uid: "left", roles: [] } as never);
    await page.waitForTimeout(100);
    expect(state.reads).toHaveLength(before);
    revoked = true;
    await emit(page, "user_private", "actor-a", "user:project-roles:updated", { project_uid: "left", roles: [] } as never);
    await expect(sidebar(page).getByRole("alert")).toBeVisible();
    await expect(sidebar(page)).not.toContainText("left card");
    expect(state.reads).toHaveLength(before + 1);
    expect(state.whole).toEqual([]);
    expect(state.errors).toEqual([]);
});
