import { expect, test, type Page, type Route } from "@playwright/test";
import type { ICardRelationSummary, ICardRelationSummariesPage } from "@/controllers/api/card/useGetCardRelationSummaries";

const fixture = "/src/pages/BoardPage/components/board/relations-tree-tests/relations.fixture.html";
const edge = (source: string, uid: string, title: string, project = "home", semantic = "contains"): ICardRelationSummary => ({
    relationship: {
        uid: `${source}-${uid}`,
        parent_card_uid: source,
        child_card_uid: uid,
        relationship_type_uid: "type",
        parent_name: "Contains",
        child_name: "Belongs to",
        machine_semantic: semantic,
        is_active: true,
        is_system_default: true,
        affects_readiness: false,
    },
    target: { uid, title, project_uid: project, project_title: project === "home" ? "Home" : "Remote board" },
});
const pageOf = (uid: string, items: ICardRelationSummary[], next: string | null = null, project = "home"): ICardRelationSummariesPage => ({
    card: { uid, title: `${uid} selected`, project_uid: project },
    items,
    next_cursor: next,
    limit: 25,
});
const firstItems = [
    edge("root", "missing", "Missing cache node"),
    edge("root", "cross", "Cross board node", "remote"),
    ...Array.from({ length: 23 }, (_, index) => edge("root", `row-${index}`, `Related ${index}`, "home", "references")),
];
async function mock(page: Page, respond: (route: Route, uid: string, cursor: string | null) => Promise<void>) {
    const reads: string[] = [];
    const failures: string[] = [];
    const errors: string[] = [];
    const otherApiReads: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
        const url = new URL(request.url());
        if (url.port === "5381" && !url.pathname.endsWith("/relation-summaries")) otherApiReads.push(url.pathname);
    });
    page.on("requestfailed", (request) => failures.push(request.url()));
    await page.route("**/relation-summaries**", async (route) => {
        const url = new URL(route.request().url());
        if (!url.pathname.endsWith("/relation-summaries")) return route.fulfill({ json: {} });
        reads.push(url.pathname + url.search);
        expect(url.searchParams.get("limit")).toBe("25");
        const uid = url.pathname.split("/").at(-2)!;
        await respond(route, uid, url.searchParams.get("cursor"));
    });
    return { reads, failures, errors, otherApiReads };
}
async function show(page: Page) {
    await page.goto(fixture);
    await page.getByRole("button", { name: "Show Relations", exact: true }).click();
}

test("native reconnect retires all protected summaries once across expanded nodes and duplicate consumers", async ({ page }) => {
    let disconnected = false;
    const reads: string[] = [];
    const pending: Route[] = [];
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route(/\/board\/(home|remote)\//, (route) => {
        const url = new URL(route.request().url());
        reads.push(url.pathname);
        expect(url.searchParams.get("limit")).toBe("25");
        expect(url.pathname.endsWith("/relation-summaries") || url.pathname.endsWith("/wikis/summaries")).toBe(true);
        if (disconnected) {
            pending.push(route);
            return;
        }
        if (url.pathname.endsWith("/wikis/summaries"))
            return route.fulfill({
                json: {
                    items: [{ wiki_uid: "private", title: "Protected Wiki before reconnect", is_public: false }],
                    next_cursor: null,
                    limit: 25,
                },
            });
        const uid = url.pathname.split("/").at(-2)!;
        return route.fulfill({ json: pageOf(uid, [edge(uid, uid === "root" ? "child" : "secret", `Protected ${uid} before reconnect`)]) });
    });
    await page.goto(`${fixture}?reconnect=1`);
    await page.getByRole("button", { name: "Show Relations", exact: true }).click();
    await expect(page.getByText("Protected root before reconnect", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Expand relationships for Protected root before reconnect", exact: true }).click();
    await expect(page.getByText("Protected child before reconnect", { exact: true })).toBeVisible();
    await expect.poll(() => reads.length).toBe(4);
    await page.evaluate(() => (window as unknown as { relationReconnect: { disconnect: () => Promise<void> } }).relationReconnect.disconnect());
    disconnected = true;
    const before = reads.length;
    const reopened = await page.evaluate(() =>
        (
            window as unknown as { relationReconnect: { reconnect: () => Promise<{ sockets: number; summaries: { data: unknown }[] }> } }
        ).relationReconnect.reconnect()
    );
    expect(reopened.sockets).toBe(2);
    expect(reopened.summaries.length).toBeGreaterThanOrEqual(3);
    expect(reopened.summaries.every(({ data }) => data === undefined)).toBe(true);
    await expect(page.getByText("Protected root before reconnect", { exact: true })).toHaveCount(0);
    await expect.poll(() => pending.length).toBeGreaterThanOrEqual(3);
    for (const route of pending) {
        const url = new URL(route.request().url());
        await route
            .fulfill({
                json: url.pathname.endsWith("/wikis/summaries")
                    ? { items: [], next_cursor: null, limit: 25 }
                    : pageOf(url.pathname.split("/").at(-2)!, []),
            })
            .catch(() => {});
    }
    await expect(page.getByText("No relationships on this card.", { exact: true })).toBeVisible();
    const refreshed = reads.slice(before);
    for (const path of new Set(refreshed)) expect(refreshed.filter((read) => read === path)).toHaveLength(1);
    expect(refreshed).toContain("/board/home/card/root/relation-summaries");
    expect(refreshed).toContain("/board/remote/card/remote-root/relation-summaries");
    expect(refreshed).toContain("/board/home/wikis/summaries");
    expect(errors).toEqual([]);
});

test("failed next-page retry preserves valid rows and requests the failed cursor", async ({ page }) => {
    const cursors: (string | null)[] = [];
    let failMore = true;
    await mock(page, async (route, uid, cursor) => {
        cursors.push(cursor);
        if (cursor && failMore) {
            failMore = false;
            return route.fulfill({ status: 503, json: {} });
        }
        return route.fulfill({
            json: pageOf(uid, [edge(uid, cursor ? "second" : "first", cursor ? "Second valid row" : "First valid row")], cursor ? null : "next"),
        });
    });
    await show(page);
    await expect(page.getByText("First valid row", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "More relationships", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByText("First valid row", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByText("Second valid row", { exact: true })).toBeVisible();
    expect(cursors).toEqual([null, "next", "next"]);
});

test("native relationship revocation retires summary metadata before authorized re-read completes", async ({ page }) => {
    let pending: Route | undefined;
    let calls = 0;
    await mock(page, async (route, uid) => {
        if (++calls > 1) {
            pending = route;
            return;
        }
        return route.fulfill({ json: pageOf(uid, [edge(uid, "revoked", "Revoked remote title", "remote")]) });
    });
    await page.route("**/card/root/relationships", (route) => route.fulfill({ status: 403, json: {} }));
    await show(page);
    await expect(page.getByText("Revoked remote title", { exact: true })).toBeVisible();
    await page.evaluate(() =>
        (window as unknown as { emitNativeSummaryEvent: (...args: unknown[]) => Promise<void> }).emitNativeSummaryEvent(
            "board",
            "home",
            "board:card:relationships:updated:home",
            { card_uid: "root", relationships_invalidated: true }
        )
    );
    await expect(page.getByText("Revoked remote title", { exact: true })).toHaveCount(0);
    await expect.poll(() => !!pending).toBe(true);
    await pending!.fulfill({ json: pageOf("root", []) });
    await expect(page.getByText("No relationships on this card.", { exact: true })).toBeVisible();
    expect(calls).toBe(2);
});

test("denied next-page access removes earlier rows and retries from current authorization", async ({ page }) => {
    const cursors: (string | null)[] = [];
    let pending: Route | undefined;
    await mock(page, async (route, uid, cursor) => {
        cursors.push(cursor);
        if (cursors.length === 2) return route.fulfill({ status: 403, json: {} });
        if (cursors.length === 3) {
            pending = route;
            return;
        }
        return route.fulfill({ json: pageOf(uid, [edge(uid, "old", "Earlier private title")], "next") });
    });
    await show(page);
    await expect(page.getByText("Earlier private title", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "More relationships", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByText("Earlier private title", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect.poll(() => !!pending).toBe(true);
    await expect(page.getByText("Earlier private title", { exact: true })).toHaveCount(0);
    await pending!.fulfill({ json: pageOf("root", [edge("root", "current", "Current authorization")]) });
    await expect(page.getByText("Current authorization", { exact: true })).toBeVisible();
    await expect(page.getByText("Earlier private title", { exact: true })).toHaveCount(0);
    expect(cursors).toEqual([null, "next", null]);
});

test("real tree reads missing-cache nodes lazily, pages branches, stops cycles and navigates native boards", async ({ page }, testInfo) => {
    const proof = await mock(page, async (route, uid, cursor) => {
        if (uid === "root")
            return route.fulfill({ json: pageOf(uid, cursor ? [edge(uid, "last", "Last paged node")] : firstItems, cursor ? null : "root-page-2") });
        if (uid === "missing") {
            const back = edge("root", "missing", "root selected");
            back.target = { uid: "root", title: "root selected", project_uid: "home" };
            return route.fulfill({
                json: pageOf(
                    uid,
                    cursor ? [edge(uid, "branch-more", "Branch page two")] : [back, edge(uid, "grandchild", "Grandchild")],
                    cursor ? null : "branch-page-2"
                ),
            });
        }
        return route.fulfill({ json: pageOf(uid, []) });
    });
    await page.goto(fixture);
    expect(proof.reads).toEqual([]);
    await page.getByRole("button", { name: "Show Relations", exact: true }).click();
    const nav = page.getByRole("navigation", { name: "Relations", exact: true });
    await expect(nav.getByRole("button", { name: "Missing cache node", exact: false }).last()).toBeVisible();
    await expect(page.getByTestId("missing-cache")).toHaveText("false");
    await expect(nav).not.toContainText("Secret private title");
    expect(await nav.innerHTML()).not.toContain("private-uid");
    expect(proof.reads).toHaveLength(1);
    const branch = nav.locator("li").filter({ has: page.getByRole("button", { name: "Missing cache node Belongs to", exact: true }) });
    await branch.getByRole("button", { name: "Expand relationships for Missing cache node", exact: true }).click();
    await expect(branch.getByRole("button", { name: "Grandchild Belongs to", exact: true })).toBeVisible();
    await expect(branch.getByRole("button", { name: "Expand relationships for root selected", exact: true })).toBeDisabled();
    await expect(branch).toContainText("Already in this branch");
    await expect(branch.getByRole("button", { name: "root selected Contains", exact: true })).toBeVisible();
    expect(proof.reads).toHaveLength(2);
    await branch.getByRole("button", { name: "More relationships", exact: true }).click();
    await expect(branch).toContainText("Branch page two");
    await branch.getByRole("button", { name: "Collapse relationships for Missing cache node", exact: true }).click();
    await expect(nav).not.toContainText("Grandchild");
    await nav.getByRole("button", { name: "More relationships", exact: true }).click();
    await expect(nav).toContainText("Last paged node");
    expect(proof.reads).toHaveLength(4);
    await nav.getByRole("button", { name: "Expand relationships for Missing cache node", exact: true }).click();
    await expect(nav).toContainText("Grandchild");
    expect(proof.reads).toHaveLength(5);
    await nav.screenshot({ path: testInfo.outputPath("expanded-tree.png") });
    await nav.getByRole("button", { name: "Cross board node Belongs to · Remote board", exact: true }).click();
    await expect(page.getByTestId("location")).toHaveText("/board/remote/cross");
    await nav.getByRole("button", { name: "Expand relationships for Cross board node", exact: true }).click();
    await expect(nav.getByText("No relationships on this card.", { exact: true })).toBeVisible();
    expect(proof.reads.at(-1)).toContain("/board/remote/card/cross/");
    await nav.getByRole("button", { name: "Full graph", exact: true }).click();
    await expect(page.getByTestId("location")).toHaveText("/board/home/graph");
    expect(proof.errors).toEqual([]);
    expect(proof.otherApiReads).toEqual([]);
    await testInfo.attach("request-receipt", { body: JSON.stringify(proof, null, 2), contentType: "application/json" });
});

test("depth cap leaves native navigation available without eager recursive reads", async ({ page }) => {
    const proof = await mock(page, async (route, uid) => {
        const n = uid === "root" ? 0 : Number(uid.slice(1));
        return route.fulfill({ json: pageOf(uid, [edge(uid, `n${n + 1}`, `Depth ${n + 1}`)]) });
    });
    await show(page);
    for (let depth = 1; depth < 8; depth++) await page.getByRole("button", { name: `Expand relationships for Depth ${depth}`, exact: true }).click();
    await expect(page.getByRole("button", { name: "Expand relationships for Depth 8", exact: true })).toBeDisabled();
    await expect(page.getByText("Open this card to continue", { exact: true })).toBeVisible();
    expect(proof.reads).toHaveLength(8);
    await page.getByRole("button", { name: "Depth 8 Belongs to", exact: true }).click();
    await expect(page.getByTestId("location")).toHaveText("/board/home/n8");
});

for (const change of ["Select other card", "Select other project", "Change actor", "Hide Relations", "Logout"]) {
    test(`pending responses are fenced after ${change}`, async ({ page }, testInfo) => {
        let release!: () => void;
        let started!: () => void;
        const pending = new Promise<void>((resolve) => (release = resolve));
        const requested = new Promise<void>((resolve) => (started = resolve));
        let count = 0;
        const proof = await mock(page, async (route, uid) => {
            count++;
            if (count === 1) {
                started();
                await pending;
                await route.fulfill({ json: pageOf(uid, [edge(uid, "stale", "STALE TITLE MUST NOT RENDER")]) }).catch(() => {});
            } else await route.fulfill({ json: pageOf(uid, [edge(uid, "fresh", "Current context title")]) });
        });
        await show(page);
        await requested;
        await page.getByRole("button", { name: change, exact: true }).click();
        release();
        if (!["Hide Relations", "Logout"].includes(change)) await expect(page.getByText("Current context title", { exact: true })).toBeVisible();
        await expect(page.getByText("STALE TITLE MUST NOT RENDER", { exact: true })).toHaveCount(0);
        await expect.poll(() => proof.failures.filter((url) => url.includes("relation-summaries")).length).toBeGreaterThan(0);
        expect(proof.errors).toEqual([]);
        await testInfo.attach("request-receipt", { body: JSON.stringify(proof, null, 2), contentType: "application/json" });
    });
}

test("collapse cancels a pending child; forbidden node uses a retry boundary without cached private content", async ({ page }) => {
    let release!: () => void;
    let started!: () => void;
    const pending = new Promise<void>((resolve) => (release = resolve));
    const requested = new Promise<void>((resolve) => (started = resolve));
    let childReads = 0;
    const proof = await mock(page, async (route, uid) => {
        if (uid === "root") return route.fulfill({ json: pageOf(uid, [edge(uid, "missing", "Missing cache node")]) });
        childReads++;
        if (childReads === 1) {
            started();
            await pending;
            return route.fulfill({ json: pageOf(uid, [edge(uid, "stale", "Collapsed stale child")]) }).catch(() => {});
        }
        if (childReads === 2) return route.fulfill({ status: 404, json: { code: "NF2001" } });
        return route.fulfill({ json: pageOf(uid, []) });
    });
    await show(page);
    await page.getByRole("button", { name: "Expand relationships for Missing cache node", exact: true }).click();
    await requested;
    await page.getByRole("button", { name: "Collapse relationships for Missing cache node", exact: true }).click();
    release();
    await expect.poll(() => proof.failures.length).toBeGreaterThan(0);
    await expect(page.getByText("Collapsed stale child", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Expand relationships for Missing cache node", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Could not load relationships");
    await expect(page.getByRole("navigation", { name: "Relations", exact: true })).not.toContainText("Secret private title");
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByText("No relationships on this card.", { exact: true })).toBeVisible();
});

test("native inaccessible prerequisites remain generic after selected-card authorization", async ({ page }, testInfo) => {
    let release!: () => void;
    let started!: () => void;
    const pending = new Promise<void>((resolve) => (release = resolve));
    const requested = new Promise<void>((resolve) => (started = resolve));
    const proof = await mock(page, async (route, uid) => {
        started();
        await pending;
        return route.fulfill({ json: pageOf(uid, []) });
    });
    await page.goto(fixture + "?blocked=1");
    await page.getByRole("button", { name: "Show Relations", exact: true }).click();
    await requested;
    const nav = page.getByRole("navigation", { name: "Relations", exact: true });
    await expect(nav.getByText("Inaccessible prerequisite", { exact: true })).toHaveCount(0);
    release();
    await expect(nav.getByRole("region", { name: "Blockers", exact: true })).toContainText("Blockers · 1");
    await expect(nav.getByText("Inaccessible prerequisite", { exact: true })).toBeVisible();
    const html = await nav.innerHTML();
    for (const secret of ["Secret private title", "private-uid", "hidden-relationship-uid"]) expect(html).not.toContain(secret);
    expect(proof.reads).toHaveLength(1);
    expect(proof.otherApiReads).toEqual([]);
    await testInfo.attach("request-receipt", { body: JSON.stringify(proof, null, 2), contentType: "application/json" });
});
