import { expect, test, type Page, type Route, type TestInfo, type WebSocketRoute } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const fixtureURL = "/src/components/Layout/changes-native-query.fixture.html";
const sidebar = (page: Page) => page.locator("section[aria-label='Changes']");
const rail = (page: Page) => page.getByRole("button", { name: "Changes", exact: true });

async function navigate(page: Page, path: string) {
    await page.evaluate((next) => {
        window.history.pushState(null, "", next);
        window.dispatchEvent(new PopStateEvent("popstate"));
    }, path);
    await expect(page.locator("[data-native-location]")).toHaveText(path);
}

async function updateSession(page: Page, token: string) {
    await page.evaluate(async (nextToken) => {
        const storePath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ storePath), import(/* @vite-ignore */ apiPath)]);
        await store.getState().updateToken(nextToken, api);
    }, token);
}

async function queryReceipt(page: Page) {
    return page.evaluate(async () => {
        const path = "/src/core/helpers/QueryClient.ts";
        const { queryClient } = await import(/* @vite-ignore */ path);
        return {
            defaults: queryClient.getDefaultOptions(),
            snapshots: queryClient
                .getQueryCache()
                .findAll({
                    predicate: (query: { queryKey: unknown[] }) =>
                        String(query.queryKey[0]).startsWith("get-cards-") || query.queryKey[0] === "unread-card-summaries",
                })
                .map(
                    (query: {
                        queryKey: unknown[];
                        state: { fetchStatus: string; status: string; isInvalidated: boolean; dataUpdateCount: number };
                        getObserversCount: () => number;
                        isActive: () => boolean;
                    }) => ({ key: query.queryKey, ...query.state, observers: query.getObserversCount(), active: query.isActive() })
                ),
        };
    });
}

async function nativeFixture(page: Page, testInfo: TestInfo) {
    const now = new Date().toISOString();
    let actor = "fixture-user";
    let revision = 1;
    let hold = false;
    let failNext = false;
    const held: { route: Route; json: unknown }[] = [];
    const boardReads: { actor: string; board: string; token: string | undefined; revision: number }[] = [];
    const reads: { actor: string; board: string; token: string | undefined; revision: number }[] = [];
    const http: { method: string; path: string }[] = [];
    const wire: { direction: string; payload: unknown }[] = [];
    const stages: { label: string; reads: number; query: unknown }[] = [];
    const errors: string[] = [];
    let peer: WebSocketRoute | undefined;
    page.on("pageerror", (error) => errors.push(error.message));
    await page.routeWebSocket(/.*/, (socket) => {
        peer = socket;
        socket.onMessage((raw) => {
            const message = JSON.parse(String(raw));
            wire.push({ direction: "client", payload: message });
            if (["subscribe", "unsubscribe"].includes(message.event)) {
                const acknowledgement = { ...message, event: message.event === "subscribe" ? "subscribed" : "unsubscribed" };
                wire.push({ direction: "server", payload: acknowledgement });
                socket.send(JSON.stringify(acknowledgement));
            }
        });
    });
    const user = () => ({
        uid: actor,
        created_at: now,
        updated_at: now,
        type: "user",
        firstname: "Fixture",
        lastname: "User",
        email: "fixture@example.com",
        username: actor,
        api_key_role_actions: ["*"],
        setting_role_actions: ["*"],
        mcp_role_actions: ["*"],
        user_groups: [],
        subemails: [],
        preferred_lang: "en",
        notification_unsubs: {},
        industry: "",
        purpose: "",
    });
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
            const method = route.request().method();
            http.push({ method, path });
            if (method === "OPTIONS") return route.fulfill({ status: 204 });
            const board = path.split("/")[2];
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture-user-session", user: user(), bots: [] };
            else if (path === `/board/${board}`) json = { project: projects().find((project) => project.uid === board) };
            else if (path === `/board/${board}/cards`) {
                boardReads.push({ actor, board, token: route.request().headers().authorization, revision });
                json = {
                    cards: [
                        {
                            uid: `${board}-card`,
                            project_uid: board,
                            project_column_uid: `${board}-column`,
                            title: `${actor} ${board} revision ${revision}`,
                            created_at: now,
                            updated_at: now,
                            order: 0,
                            member_uids: [],
                            labels: [],
                            relationships: [],
                            has_unread_change: true,
                            last_change_seq: revision,
                            last_change_at: now,
                        },
                    ],
                    columns: [{ uid: `${board}-column`, project_uid: board, name: `${board} column`, order: 0, created_at: now, updated_at: now }],
                    checklists: [],
                    global_relationships: [],
                };
            } else if (path === `/board/${board}/unread-card-summaries`) {
                reads.push({ actor, board, token: route.request().headers().authorization, revision });
                if (failNext) {
                    failNext = false;
                    return route.fulfill({ status: 503, json: { detail: "Labelled summary failure" } });
                }
                json = {
                    items: [
                        {
                            uid: `${board}-card`,
                            project_uid: board,
                            title: `${actor} ${board} revision ${revision}`,
                            has_unread_change: true,
                            unread_change_count: revision,
                            unread_change_count_status: "exact",
                            latest_change: { seq: revision, target_type: "card", target_uid: null, at: now },
                        },
                    ],
                    next_cursor: null,
                    limit: 50,
                };
                if (hold) {
                    held.push({ route, json });
                    return;
                }
            } else if (path.startsWith("/metadata/")) json = { metadata: {} };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project")) json = { projects: projects(), columns: [], records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            await route.fulfill({ json });
        }
    );
    return {
        reads,
        boardReads,
        held,
        errors,
        stages,
        setActor: (value: string) => (actor = value),
        setRevision: (value: number) => (revision = value),
        failNext: () => (failNext = true),
        hold: () => (hold = true),
        releaseOldest: async () => {
            const pending = held.shift()!;
            await pending.route.fulfill({ json: pending.json });
        },
        release: async () => {
            hold = false;
            for (const pending of held.splice(0)) await pending.route.fulfill({ json: pending.json });
        },
        emit: (board: string) => {
            const message = {
                topic: "board",
                topic_id: board,
                event: `board:card:checklist:progress:changed:${board}`,
                data: { card_uid: `${board}-card`, checkitems: [] },
            };
            wire.push({ direction: "server", payload: message });
            peer!.send(JSON.stringify(message));
        },
        settle: async () => {
            await expect
                .poll(async () => (await queryReceipt(page)).snapshots.every((query: { fetchStatus: string }) => query.fetchStatus === "idle"))
                .toBe(true);
            // A bounded quiet window detects deferred mount requests after React commits.
            await page.waitForTimeout(200);
        },
        record: async (label: string) => stages.push({ label, reads: reads.length, query: await queryReceipt(page) }),
        save: async () => {
            const path = testInfo.outputPath("lifecycle.json");
            await writeFile(
                path,
                JSON.stringify(
                    {
                        scope: "Local native QueryClient with labelled HTTP and WebSocket fixtures; not production or authenticated E2E",
                        reads,
                        boardReads,
                        http,
                        wire,
                        stages,
                        errors,
                    },
                    null,
                    2
                )
            );
            await testInfo.attach("labelled-native-query-lifecycle", { path, contentType: "application/json" });
        },
    };
}

test("native board panel mounts reuse its active snapshot; refresh, wire invalidation and route remount remain fresh", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await page.goto(fixtureURL);
        await expect(rail(page)).toContainText("1", { timeout: 45_000 });
        await fixture.settle();
        expect((await queryReceipt(page)).defaults).toEqual({});
        await fixture.record("initial board");
        await rail(page).click();
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 1/ })).toBeVisible();
        await fixture.settle();
        await fixture.record("open Changes");
        await rail(page).click();
        await expect(sidebar(page)).toBeHidden();
        await fixture.settle();
        await fixture.record("hide Changes");
        await rail(page).click();
        await expect(sidebar(page)).toBeVisible();
        await fixture.settle();
        await fixture.record("reopen hidden Changes");
        await page.getByRole("button", { name: "Explorer", exact: true }).click();
        await expect(sidebar(page)).toHaveCount(0);
        await rail(page).click();
        await expect(sidebar(page)).toBeVisible();
        await fixture.settle();
        await fixture.record("remount Changes after Explorer");
        expect(fixture.stages.map(({ reads }) => reads)).toEqual([0, 1, 1, 2, 3]);
        expect(fixture.boardReads).toHaveLength(1);

        const beforeRefresh = fixture.reads.length;
        fixture.setRevision(2);
        await sidebar(page).getByRole("button", { name: "Refresh", exact: true }).click();
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 2/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads.length).toBe(beforeRefresh + 1);
        expect(fixture.boardReads).toHaveLength(1);
        await fixture.record("explicit Refresh");

        fixture.setRevision(3);
        fixture.emit("left");
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 3/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads.length).toBe(beforeRefresh + 2);
        await fixture.record("native socket invalidation");
        fixture.emit("right");
        await fixture.settle();
        expect(fixture.reads.length).toBe(beforeRefresh + 2);

        await navigate(page, "/empty");
        await expect(page.getByText("Fixture route unmounted")).toBeVisible();
        fixture.setRevision(4);
        await navigate(page, "/board/left");
        await expect(page.getByText("fixture-user left revision 4", { exact: true }).first()).toBeVisible();
        await fixture.settle();
        expect(fixture.reads.length).toBe(beforeRefresh + 2);
        expect(fixture.boardReads).toHaveLength(3);
        await fixture.record("meaningful board route remount");
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

test("Changes fetches without a cache and refreshes a stale snapshot when it is the only active owner", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await page.goto(`${fixtureURL}?initial=/changes/left`);
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 1/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(2);
        expect(fixture.boardReads).toHaveLength(0);
        await fixture.record("standalone cold cache");
        await navigate(page, "/empty");
        await expect(sidebar(page)).toHaveCount(0);
        fixture.setRevision(2);
        await navigate(page, "/changes/left");
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 2/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(3);
        expect(fixture.boardReads).toHaveLength(0);
        await fixture.record("standalone stale remount");
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

test("native board snapshots remain isolated across delayed actor, session and board transitions", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await page.goto(fixtureURL);
        await expect(rail(page)).toContainText("1", { timeout: 45_000 });
        await rail(page).click();
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 1/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(1);

        fixture.hold();
        fixture.setRevision(2);
        await sidebar(page).getByRole("button", { name: "Refresh", exact: true }).click();
        await expect.poll(() => fixture.held.length).toBe(1);
        fixture.setActor("other-user");
        fixture.setRevision(3);
        await updateSession(page, "other-user-session");
        // Identity replacement remounts the workspace and resets its selected panel to Explorer.
        await rail(page).click();
        await expect(sidebar(page)).toBeVisible();
        await expect.poll(() => fixture.held.length).toBe(3);
        await expect(sidebar(page).getByRole("button", { name: /fixture-user/ })).toHaveCount(0);
        await fixture.releaseOldest();
        await expect(sidebar(page).getByRole("button", { name: /fixture-user/ })).toHaveCount(0);
        await fixture.release();
        await expect(sidebar(page).getByRole("button", { name: /other-user left revision 3/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(4);
        await fixture.record("actor replacement rejects delayed previous actor");

        fixture.hold();
        fixture.setRevision(4);
        await updateSession(page, "renewed-user-session");
        await expect(page.getByText("other-user left revision 4", { exact: true }).first()).toBeVisible();
        if (!(await sidebar(page).isVisible())) await rail(page).click();
        await expect(sidebar(page)).toBeVisible();
        await expect.poll(() => fixture.held.length).toBe(2);
        await expect(sidebar(page).getByRole("button", { name: /revision 3/ })).toHaveCount(0);
        await fixture.release();
        await expect(sidebar(page).getByRole("button", { name: /other-user left revision 4/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(6);
        await fixture.record("same actor new session fetches independently");

        await page.getByRole("button", { name: "Explorer", exact: true }).click();
        fixture.hold();
        fixture.setRevision(5);
        await page.locator("[data-workbench-explorer]").getByRole("button", { name: "right board", exact: true }).first().click();
        await rail(page).click();
        await expect.poll(() => fixture.held.length).toBe(1);
        await expect(sidebar(page).getByRole("button", { name: /left revision/ })).toHaveCount(0);
        await fixture.release();
        await expect(sidebar(page).getByRole("button", { name: /other-user right revision 5/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(7);
        await fixture.record("new board excludes previous board snapshot");
        expect(fixture.reads.map(({ actor, board, token }) => ({ actor, board, token }))).toEqual([
            { actor: "fixture-user", board: "left", token: "Bearer fixture-user-session" },
            { actor: "fixture-user", board: "left", token: "Bearer fixture-user-session" },
            { actor: "other-user", board: "left", token: "Bearer other-user-session" },
            { actor: "other-user", board: "left", token: "Bearer other-user-session" },
            { actor: "other-user", board: "left", token: "Bearer renewed-user-session" },
            { actor: "other-user", board: "left", token: "Bearer renewed-user-session" },
            { actor: "other-user", board: "right", token: "Bearer renewed-user-session" },
        ]);
        await page.evaluate(async () => {
            const path = "/src/core/stores/AuthStore.ts";
            const { default: store } = await import(/* @vite-ignore */ path);
            store.getState().removeToken();
        });
        await expect(sidebar(page)).toHaveCount(0);
        expect((await queryReceipt(page)).snapshots).toEqual([]);
        await fixture.record("logout clears native query cache");
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.release();
        await fixture.save();
    }
});

test("an invalidated or failed cached snapshot still refetches on Changes mount without automatic retries", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await page.goto(fixtureURL);
        await expect(rail(page)).toContainText("1", { timeout: 45_000 });
        await fixture.settle();
        fixture.setRevision(2);
        await page.evaluate(async () => {
            const path = "/src/core/helpers/QueryClient.ts";
            const { queryClient } = await import(/* @vite-ignore */ path);
            await queryClient.invalidateQueries({ queryKey: ["get-cards-left"], refetchType: "none" });
        });
        expect(fixture.boardReads).toHaveLength(1);
        expect(fixture.reads).toHaveLength(0);
        await rail(page).click();
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 2/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(1);
        expect(fixture.boardReads).toHaveLength(1);
        await fixture.record("explicit invalidation survives active owner optimization");

        fixture.failNext();
        await sidebar(page).getByRole("button", { name: "Refresh", exact: true }).click();
        await expect
            .poll(
                async () => (await queryReceipt(page)).snapshots.find((query: { key: unknown[] }) => query.key[0] === "unread-card-summaries")?.status
            )
            .toBe("error");
        await fixture.settle();
        expect(fixture.reads).toHaveLength(2);
        expect(fixture.boardReads).toHaveLength(1);
        await fixture.record("failed refresh has zero automatic retries");
        fixture.setRevision(4);
        await page.getByRole("button", { name: "Explorer", exact: true }).click();
        await expect(sidebar(page)).toHaveCount(0);
        await rail(page).click();
        await expect(sidebar(page).getByRole("button", { name: /fixture-user left revision 4/ })).toBeVisible();
        await fixture.settle();
        expect(fixture.reads).toHaveLength(3);
        expect(fixture.boardReads).toHaveLength(1);
        await fixture.record("failed cached query refetches on remount");
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});
