import { expect, test, type Page, type Route } from "@playwright/test";

const fixture = "/src/pages/BoardPage/components/board/wiki-summary-tests/WikiSummary.fixture.html";
type Summary = { wiki_uid: string; title: string; is_public: boolean };
const records = Array.from({ length: 62 }, (_, index) => ({
    wiki_uid: `wiki-${index}`,
    title: `Wiki ${String(index).padStart(2, "0")}`,
    is_public: index % 11 !== 0,
    deleted: index === 2,
    body: `BODY MUST NEVER LOAD ${index}`,
}));
const visible = records.filter((record) => record.is_public && !record.deleted);
const summaries = visible.map(({ wiki_uid, title, is_public }) => ({ wiki_uid, title, is_public }));
const response = (items: Summary[], cursor: string | null = null) => ({ items, next_cursor: cursor, limit: 25 });
const json = (route: Route, data: unknown, status = 200) => route.fulfill({ status, json: data });
const row = (page: Page, title: string) =>
    page.getByRole("navigation", { name: "Wiki", exact: true }).getByRole("button", { name: title, exact: true });
const singleton = (title: string) => response([{ wiki_uid: title.replaceAll(" ", "-"), title, is_public: false }]);

test("native assignee revocation clears a panel-only private Wiki before reauthorization", async ({ page }) => {
    let pending: Route | undefined;
    let calls = 0;
    const reads: string[] = [];
    await page.route(/\/board\/project-a\//, (route) => {
        reads.push(new URL(route.request().url()).pathname);
        if (!route.request().url().includes("/wikis/summaries")) return json(route, {}, 503);
        if (++calls > 1) {
            pending = route;
            return;
        }
        return json(route, response([{ wiki_uid: "private", title: "Revoked private Wiki", is_public: false }]));
    });
    await page.goto(fixture);
    await expect(row(page, "Revoked private Wiki")).toBeVisible();
    await page.evaluate(() =>
        (window as unknown as { emitNativeSummaryEvent: (...args: unknown[]) => Promise<void> }).emitNativeSummaryEvent(
            "board_wiki",
            "project-a",
            "board:wiki:assignees:updated:private",
            { assigned_members: [] }
        )
    );
    await expect(row(page, "Revoked private Wiki")).toHaveCount(0);
    await expect.poll(() => !!pending).toBe(true);
    await json(pending!, response([]));
    await expect(page.getByText("No accessible wikis in this project.", { exact: true })).toBeVisible();
    expect(reads).toEqual(["/board/project-a/wikis/summaries", "/board/project-a/wikis/summaries"]);
});

for (const change of [
    {
        name: "public visibility revocation",
        topic: "board_wiki",
        id: "project-a",
        event: "public:changed:known",
        data: { wiki: { uid: "known", is_public: false } },
    },
    { name: "deletion", topic: "board_wiki", id: "project-a", event: "deleted:project-a", data: { uid: "known" } },
    { name: "public title edit", topic: "board_wiki", id: "project-a", event: "details:changed:known", data: { title: "Fresh authorized Wiki" } },
    {
        name: "private title edit",
        topic: "board_wiki_private",
        id: "known",
        event: "details:changed:known",
        data: { title: "Fresh authorized Wiki" },
    },
    {
        name: "creation",
        topic: "board_wiki",
        id: "project-a",
        event: "created:project-a",
        data: { wiki: { uid: "new", title: "Fresh authorized Wiki" } },
    },
    {
        name: "unknown Wiki assignment grant",
        topic: "board_wiki",
        id: "project-a",
        event: "assignees:updated:never-loaded",
        data: { assigned_members: [{ uid: "reader-a" }] },
    },
    {
        name: "unknown Wiki made public",
        topic: "board_wiki",
        id: "project-a",
        event: "public:changed:never-loaded",
        data: { wiki: { uid: "never-loaded", is_public: true } },
    },
]) {
    test(`native ${change.name} reauthorizes bounded summaries without full model hydration`, async ({ page }) => {
        let calls = 0;
        let pending: Route | undefined;
        const reads: string[] = [];
        await page.route(/\/board\/project-a\//, (route) => {
            reads.push(new URL(route.request().url()).pathname);
            if (!route.request().url().includes("/wikis/summaries")) return json(route, {}, 503);
            if (++calls > 1) {
                pending = route;
                return;
            }
            return json(route, response([{ wiki_uid: "known", title: "Previous Wiki metadata", is_public: change.name !== "private title edit" }]));
        });
        await page.goto(fixture);
        await expect(row(page, "Previous Wiki metadata")).toBeVisible();
        await page.evaluate(
            (event) =>
                (window as unknown as { emitNativeSummaryEvent: (...args: unknown[]) => Promise<void> }).emitNativeSummaryEvent(
                    event.topic,
                    event.id,
                    `board:wiki:${event.event}`,
                    event.data
                ),
            change
        );
        await expect(row(page, "Previous Wiki metadata")).toHaveCount(0);
        await expect.poll(() => !!pending).toBe(true);
        await json(pending!, singleton("Fresh authorized Wiki"));
        await expect(row(page, "Fresh authorized Wiki")).toBeVisible();
        expect(reads).toEqual(["/board/project-a/wikis/summaries", "/board/project-a/wikis/summaries"]);
        expect(await page.evaluate(() => (window as unknown as { summaryModelCounts: () => unknown }).summaryModelCounts())).toEqual({
            wikis: 0,
            cards: 0,
        });
    });
}

test("unrelated native project and editor presence events do not invalidate current Wiki summaries", async ({ page }) => {
    let calls = 0;
    await page.route("**/board/**/wikis/summaries**", (route) => {
        calls++;
        return json(route, singleton("Current authorized Wiki"));
    });
    await page.goto(fixture);
    await expect(row(page, "Current authorized Wiki")).toBeVisible();
    await page.evaluate(async () => {
        const emit = (window as unknown as { emitNativeSummaryEvent: (...args: unknown[]) => Promise<void> }).emitNativeSummaryEvent;
        await emit("board_wiki", "project-b", "board:wiki:created:project-b", { wiki: { uid: "other" } });
        await emit("board_wiki", "project-a", "board:wiki:editor:users:known", { users: [] });
    });
    await expect(row(page, "Current authorized Wiki")).toBeVisible();
    expect(calls).toBe(1);
});

test("native page and summary share one topic and deduplicate exact-converter plus wildcard resets", async ({ page }) => {
    let calls = 0;
    await page.route("**/board/**/wikis/summaries**", (route) => json(route, singleton(`Authorized Wiki ${++calls}`)));
    await page.goto(`${fixture}?legacy=1`);
    await expect(row(page, "Authorized Wiki 1")).toBeVisible();
    const create = (uid: string) =>
        page.evaluate(
            (createdUID) =>
                (window as unknown as { emitNativeSummaryEvent: (...args: unknown[]) => Promise<void> }).emitNativeSummaryEvent(
                    "board_wiki",
                    "project-a",
                    "board:wiki:created:project-a",
                    {
                        wiki: {
                            uid: createdUID,
                            project_uid: "project-a",
                            title: "Native full Wiki",
                            is_public: true,
                            created_at: new Date().toISOString(),
                            updated_at: new Date().toISOString(),
                            assigned_members: [],
                            order: 0,
                            forbidden: false,
                        },
                    }
                ),
            uid
        );
    await create("native-one");
    await expect(row(page, "Authorized Wiki 2")).toBeVisible();
    expect(calls).toBe(2);
    expect(await page.evaluate(() => (window as unknown as { summaryModelCounts: () => unknown }).summaryModelCounts())).toEqual({
        wikis: 1,
        cards: 0,
    });
    await page.getByRole("button", { name: "Toggle native owner", exact: true }).click();
    await page.evaluate(() =>
        (window as unknown as { emitNativeSummaryEvent: (...args: unknown[]) => Promise<void> }).emitNativeSummaryEvent(
            "board_wiki",
            "project-a",
            "board:wiki:assignees:updated:newly-granted",
            { assigned_members: [{ uid: "reader-a" }] }
        )
    );
    await expect(row(page, "Authorized Wiki 3")).toBeVisible();
    await page.getByRole("button", { name: "Toggle native owner", exact: true }).click();
    await page.getByRole("button", { name: "Toggle panel", exact: true }).click();
    await create("native-two");
    expect(await page.evaluate(() => (window as unknown as { summaryModelCounts: () => unknown }).summaryModelCounts())).toEqual({
        wikis: 2,
        cards: 0,
    });
    expect(calls).toBe(3);
});

test("initial and explicit More requests stay bounded; hidden records and bodies never enter sidebar", async ({ page }, testInfo) => {
    const calls: string[] = [];
    await page.route(/\/board\/project-[ab]\//, async (route) => {
        const url = new URL(route.request().url());
        calls.push(url.pathname + url.search);
        if (!url.pathname.endsWith("/wikis/summaries")) return json(route, {}, 503);
        expect(url.searchParams.get("limit")).toBe("25");
        const offset = Number(url.searchParams.get("cursor") ?? 0);
        const items = summaries.slice(offset, offset + 25);
        return json(route, response(items, offset + 25 < summaries.length ? String(offset + 25) : null));
    });
    await page.goto(fixture);
    await expect.poll(() => calls[0]).toBe("/board/project-a/wikis/summaries?limit=25");
    await expect(row(page, summaries[0].title)).toBeVisible();
    await expect(page.locator("nav button[title]")).toHaveCount(25);
    expect(calls).toHaveLength(1);
    await expect(row(page, summaries[25].title)).toHaveCount(0);
    await page.getByRole("button", { name: "More", exact: true }).press("Enter");
    await expect(page.locator("nav button[title]")).toHaveCount(50);
    expect(calls).toHaveLength(2);
    await page.getByRole("button", { name: "More", exact: true }).click();
    await expect(page.locator("nav button[title]")).toHaveCount(summaries.length);
    await expect(page.getByRole("button", { name: "More", exact: true })).toHaveCount(0);
    for (const hidden of records.filter((record) => !record.is_public || record.deleted)) {
        await expect(row(page, hidden.title)).toHaveCount(0);
    }
    expect(calls).toHaveLength(3);
    await expect(page.locator("body")).not.toContainText("BODY MUST NEVER LOAD");
    await page.screenshot({ path: testInfo.outputPath("wiki-summary-paged.png"), fullPage: true });
});

test("row navigation keeps native detail URL, current-page highlight and mobile dismissal callback", async ({ page }) => {
    await page.route(/\/board\/project-[ab]\//, (route) => json(route, response(summaries.slice(0, 2))));
    await page.goto(fixture);
    await row(page, summaries[1].title).press("Enter");
    await expect(page.getByTestId("route")).toHaveText(`/board/project-a/wiki/${summaries[1].wiki_uid}`);
    await expect(row(page, summaries[1].title)).toHaveAttribute("aria-current", "page");
    await expect(row(page, summaries[0].title)).not.toHaveAttribute("aria-current");
    await expect(page.getByTestId("navigations")).toHaveText("1");
    await page.getByRole("button", { name: "Open wiki", exact: true }).click();
    await expect(page.getByTestId("route")).toHaveText("/board/project-a/wiki");
    await expect(page.getByTestId("navigations")).toHaveText("2");
});

test("initial failure retries without false empty state; More failure retries only that page", async ({ page }) => {
    const calls: string[] = [];
    let failInitial = true;
    let failMore = true;
    await page.route("**/board/**/wikis/summaries**", (route) => {
        const cursor = new URL(route.request().url()).searchParams.get("cursor") ?? "first";
        calls.push(cursor);
        if (cursor === "first" && failInitial) {
            failInitial = false;
            return json(route, {}, 503);
        }
        if (cursor === "more" && failMore) {
            failMore = false;
            return json(route, {}, 503);
        }
        return json(route, cursor === "first" ? response(summaries.slice(0, 25), "more") : response(summaries.slice(25)));
    });
    await page.goto(fixture);
    await expect(page.getByRole("alert")).toContainText("Could not load wikis");
    await expect(page.getByText("No wikis", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Retry", exact: true }).press("Enter");
    await expect(row(page, summaries[0].title)).toBeVisible();
    await page.getByRole("button", { name: "More", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(row(page, summaries[0].title)).toBeVisible();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.locator("nav button[title]")).toHaveCount(summaries.length);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(calls).toEqual(["first", "first", "more", "more"]);
});

for (const change of ["Switch actor", "Switch project", "Rotate session"]) {
    test(`${change} rejects a delayed previous response and clears previous rows`, async ({ page }) => {
        let delayed: Route | undefined;
        let calls = 0;
        await page.route("**/board/**/wikis/summaries**", (route) => {
            calls++;
            if (calls === 2) {
                delayed = route;
                return;
            }
            return json(route, singleton(calls === 1 ? "Old private wiki" : "Current wiki"));
        });
        await page.goto(fixture);
        await expect(row(page, "Old private wiki")).toBeVisible();
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await expect.poll(() => !!delayed).toBe(true);
        await page.getByRole("button", { name: change, exact: true }).click();
        await expect(row(page, "Current wiki")).toBeVisible();
        await expect(row(page, "Old private wiki")).toHaveCount(0);
        await json(delayed!, singleton("Late old-user private wiki")).catch(() => {});
        await expect(row(page, "Late old-user private wiki")).toHaveCount(0);
        await expect(row(page, "Current wiki")).toBeVisible();
    });
}

test("logout and returning to a prior actor never reuse that actor's private cache", async ({ page }) => {
    let calls = 0;
    let returning: Route | undefined;
    await page.route("**/board/**/wikis/summaries**", (route) => {
        calls++;
        if (calls === 3) {
            returning = route;
            return;
        }
        return json(route, singleton(calls === 1 ? "First actor private wiki" : "Second actor wiki"));
    });
    await page.goto(fixture);
    await expect(row(page, "First actor private wiki")).toBeVisible();
    await page.getByRole("button", { name: "Switch actor", exact: true }).click();
    await expect(row(page, "Second actor wiki")).toBeVisible();
    await page.getByRole("button", { name: "Log out", exact: true }).click();
    await expect(row(page, "Second actor wiki")).toHaveCount(0);
    await page.getByRole("button", { name: "Return actor", exact: true }).click();
    await expect.poll(() => !!returning).toBe(true);
    await expect(row(page, "First actor private wiki")).toHaveCount(0);
    await json(returning!, singleton("Fresh actor wiki"));
    await expect(row(page, "Fresh actor wiki")).toBeVisible();
});

test("temporary refresh failure keeps same-actor rows; revoked access removes cached metadata", async ({ page }) => {
    let calls = 0;
    await page.route("**/board/**/wikis/summaries**", (route) => {
        calls++;
        return calls === 1 ? json(route, singleton("Previously readable")) : json(route, {}, calls === 2 ? 503 : 403);
    });
    await page.goto(fixture);
    await expect(row(page, "Previously readable")).toBeVisible();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(row(page, "Previously readable")).toBeVisible();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect.poll(() => calls).toBe(3);
    await expect(row(page, "Previously readable")).toHaveCount(0);
});

test("unmounted panel performs no Wiki read and mounts lazily", async ({ page }) => {
    const calls: string[] = [];
    await page.route(/\/board\/project-[ab]\//, (route) => {
        calls.push(route.request().url());
        return json(route, singleton("Lazy wiki"));
    });
    await page.goto(`${fixture}?inactive=1`);
    await expect(page.getByTestId("actor")).toHaveText("reader-a");
    expect(calls).toEqual([]);
    await page.getByRole("button", { name: "Toggle panel", exact: true }).click();
    await expect(row(page, "Lazy wiki")).toBeVisible();
    expect(calls).toHaveLength(1);
});
