import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page, canCrossBoard = true) {
    page.on("pageerror", (error) => console.error("Split fixture page error:", error.message));
    const now = new Date().toISOString();
    const user = {
        uid: "fixture-user",
        created_at: now,
        updated_at: now,
        type: "user",
        firstname: "Fixture",
        lastname: "User",
        email: "fixture@example.com",
        username: "fixture",
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
    const projects = ["left", "right", "third"].map((uid) => ({
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
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const path = new URL(route.request().url()).pathname;
            const origin = route.request().headers().origin || "http://127.0.0.1:4196";
            const headers = {
                "access-control-allow-origin": origin,
                "access-control-allow-credentials": "true",
                "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                "access-control-allow-headers": "authorization,content-type,content-encoding",
            };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            const uid = path.split("/")[2];
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user, bots: [] };
            else if (path === `/board/${uid}`)
                json = { project: projects.find((project) => project.uid === uid), project_bot_scopes: [], project_bot_schedules: [] };
            else if (path === `/board/${uid}/cards`)
                json = {
                    cards: [
                        {
                            uid: `${uid}-card`,
                            project_uid: uid,
                            project_column_uid: `${uid}-column-0`,
                            project_column_name: `${uid} column 0`,
                            title: `${uid} draggable card`,
                            created_at: now,
                            updated_at: now,
                            order: 0,
                            can_cross_board: canCrossBoard,
                            creator: { uid: "ordinary-bot", type: "bot", name: "Ordinary bot", avatar: null, created_at: now },
                            count_comment: 0,
                            member_uids: [],
                            current_auth_role_actions: ["*"],
                            project_members: [],
                            labels: [],
                            relationships: [],
                            has_description: false,
                        },
                    ],
                    columns: Array.from({ length: 10 }, (_, order) => ({
                        uid: `${uid}-column-${order}`,
                        project_uid: uid,
                        name: `${uid} column ${order}`,
                        order,
                        created_at: now,
                        updated_at: now,
                        color: "#444444",
                    })),
                    checklists: [],
                    global_relationships: [],
                };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project"))
                json = {
                    projects,
                    columns: projects.flatMap((project) =>
                        Array.from({ length: 10 }, (_, order) => ({
                            uid: `${project.uid}-column-${order}`,
                            project_uid: project.uid,
                            name: `${project.uid} column ${order}`,
                            order,
                            created_at: now,
                            updated_at: now,
                            color: "#444444",
                        }))
                    ),
                    records: [],
                    collections: [],
                };
            else if (path.includes("apps")) json = { apps: [] };
            return route.fulfill({ json, headers });
        }
    );
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    await expect(page.getByText("left column 0", { exact: true }).first()).toBeVisible({ timeout: 45_000 });
    await expect(page.locator("[data-board-pane-footer]")).toHaveCount(0);
}

async function dropCard(page: Page, eligible = true) {
    if (!(await page.locator("[data-workbench-explorer]").isVisible())) await page.getByRole("button", { name: "Explorer", exact: true }).click();
    await page.getByRole("button", { name: "Open right board in split view" }).first().click({ force: true });
    const panes = page.locator("[data-board-pane]");
    await expect(panes).toHaveCount(2);
    const source = panes.first().getByText("left draggable card", { exact: true });
    const destination = panes.nth(1).locator("[data-cross-board-column=right-column-0]");
    await expect(source).toBeVisible();
    await expect(destination).toBeVisible();
    const sourceBox = await source.boundingBox();
    const destinationBox = await destination.boundingBox();
    if (!sourceBox || !destinationBox) throw new Error("Missing native drag surfaces");
    await page.mouse.move(sourceBox.x + 20, sourceBox.y + 10);
    await page.mouse.down();
    await page.mouse.move(sourceBox.x + 30, sourceBox.y + 15, { steps: 5 });
    await page.mouse.move(destinationBox.x + 130, destinationBox.y + 180, { steps: 30 });
    await page.mouse.up();
    await expect(page.getByRole("dialog")).toHaveCount(eligible ? 1 : 0);
}

function interceptAction(page: Page, handler: (body: Record<string, unknown>, count: number) => { status: number; json: unknown }) {
    const requests: Record<string, unknown>[] = [];
    const changes: string[] = [];
    page.on("request", (request) => {
        if (request.method() !== "GET" && request.url().includes("/order")) changes.push(request.url());
    });
    return page
        .route("**/board/right/card/cross-board", async (route) => {
            if (route.request().method() === "OPTIONS")
                return route.fulfill({
                    status: 204,
                    headers: {
                        "access-control-allow-origin": "http://127.0.0.1:4197",
                        "access-control-allow-credentials": "true",
                        "access-control-allow-methods": "POST,OPTIONS",
                        "access-control-allow-headers": "authorization,content-type,content-encoding",
                    },
                });
            const body = route.request().postDataJSON();
            requests.push(body);
            const result = handler(body, requests.length);
            await route.fulfill({
                ...result,
                headers: { "access-control-allow-origin": "http://127.0.0.1:4197", "access-control-allow-credentials": "true" },
            });
        })
        .then(() => ({ requests, changes }));
}

test("native pane drop links without reorder and refreshes only destination", async ({ page }) => {
    await setup(page);
    const refreshes: string[] = [];
    page.on("request", (request) => {
        if (new URL(request.url()).pathname.endsWith("/cards") && request.method() === "GET") refreshes.push(request.url());
    });
    const calls = await interceptAction(page, () => ({ status: 201, json: { card: { uid: "mirror" }, created: true } }));
    await dropCard(page);
    await expect(page.getByText("Move is not supported yet.")).toBeVisible();
    await page.getByRole("button", { name: "Link card", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Cross-board action completed." })).toBeVisible();
    expect(refreshes.filter((url) => url.includes("/board/right/"))).toHaveLength(2);
    expect(refreshes.filter((url) => url.includes("/board/left/"))).toHaveLength(0);
    expect(calls.requests).toHaveLength(1);
    expect(calls.requests[0]).toMatchObject({
        source_project_uid: "left",
        source_card_uid: "left-card",
        destination_column_uid: "right-column-0",
        operation: "link",
    });
    expect(calls.changes).toEqual([]);
    await expect(page.getByText("left draggable card", { exact: true })).toBeVisible();
});

test("clone retry preserves UUID; new audience confirmation requires explicit consent", async ({ page }) => {
    await setup(page);
    const calls = await interceptAction(page, (_body, count) =>
        count === 1
            ? { status: 503, json: {} }
            : count === 2
              ? {
                    status: 409,
                    json: {
                        confirmation_required: true,
                        confirmation_token: "audience-revision",
                        created: false,
                        private_payload: "must-not-display",
                    },
                }
              : {
                    status: 201,
                    json: { card: { uid: "clone" }, created: true, excluded: { member_uids: ["excluded-member"], label_uids: ["excluded-label"] } },
                }
    );
    await dropCard(page);
    await page.getByRole("button", { name: "Clone card", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Retry or cancel");
    await page.getByRole("button", { name: "Clone card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Confirm exposure" })).toBeVisible();
    expect(calls.requests).toHaveLength(2);
    await expect(page.getByText("must-not-display")).toHaveCount(0);
    await page.getByRole("button", { name: "Confirm exposure" }).click();
    const exclusions = page.getByRole("status").filter({ hasText: "Excluded mappings" });
    await expect(exclusions).toContainText("Members: 1");
    await expect(exclusions).toContainText("Labels: 1");
    await expect(page.getByText("excluded-member", { exact: false })).toHaveCount(0);
    await expect(page.getByText("excluded-label", { exact: false })).toHaveCount(0);
    expect(new Set(calls.requests.map((body) => body.request_uid)).size).toBe(1);
    expect(calls.requests[2].confirmation_token).toBe("audience-revision");
    expect(calls.changes).toEqual([]);
});

test("same-reader clone completes immediately; unrelated conflicts never request exposure", async ({ page }) => {
    await setup(page);
    const calls = await interceptAction(page, () => ({ status: 201, json: { card: { uid: "clone" }, created: true } }));
    await dropCard(page);
    await page.getByRole("button", { name: "Clone card", exact: true }).click();
    await expect(page.getByText("Cross-board action completed.", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm exposure" })).toHaveCount(0);
    expect(calls.requests).toHaveLength(1);
});

for (const status of [403, 404, 409])
    test(`native ${status} denial reveals no server payload and cancellation never retries`, async ({ page }) => {
        await setup(page);
        const calls = await interceptAction(page, () => ({
            status,
            json: { title: "private-source-title", confirmation_token: "unrequested-token" },
        }));
        await dropCard(page);
        await page.getByRole("button", { name: "Clone card", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByText("private-source-title")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Confirm exposure" })).toHaveCount(0);
        await page.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(calls.requests).toHaveLength(1);
    });

test("declining exposure creates no second request", async ({ page }) => {
    await setup(page);
    const calls = await interceptAction(page, () => ({
        status: 409,
        json: { confirmation_required: true, confirmation_token: "revision", created: false },
    }));
    await dropCard(page);
    await page.getByRole("button", { name: "Clone card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Confirm exposure" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(calls.requests).toHaveLength(1);
});

test("closing the destination aborts an in-flight result and preserves the source pane", async ({ page }) => {
    await setup(page);
    let release: (() => void) | undefined;
    const waiting = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/board/right/card/cross-board", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fallback();
        await waiting;
        await route
            .fulfill({
                status: 201,
                json: { card: { uid: "stale-result", title: "private-stale-payload" }, created: true },
                headers: { "access-control-allow-origin": "http://127.0.0.1:4197", "access-control-allow-credentials": "true" },
            })
            .catch(() => {});
    });
    await dropCard(page);
    const sent = page.waitForRequest("**/board/right/card/cross-board");
    await page.getByRole("button", { name: "Clone card", exact: true }).click();
    await sent;
    await page
        .getByRole("button", { name: "Close right board", exact: true, includeHidden: true })
        .evaluate((button: HTMLButtonElement) => button.click());
    release?.();
    await expect(page.locator("[data-board-pane]")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByText("left draggable card", { exact: true })).toBeVisible();
    await expect(page.getByText("private-stale-payload")).toHaveCount(0);
});

test("stale source removal prevents any API action", async ({ page }) => {
    await setup(page);
    const calls = await interceptAction(page, () => ({ status: 201, json: { card: { uid: "unexpected" }, created: true } }));
    await dropCard(page);
    await page.evaluate(async () => {
        const moduleURL = "/src/core/models/ProjectCard.ts";
        const cards = await import(moduleURL);
        cards.Model.deleteModel("left-card");
    });
    await page.getByRole("button", { name: "Clone card", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    expect(calls.requests).toHaveLength(0);
});

test("opening and cancelling native drop selection sends no mutation", async ({ page }) => {
    await setup(page);
    const mutations: string[] = [];
    page.on("request", (request) => {
        if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method()) && new URL(request.url()).pathname.startsWith("/board/")) {
            mutations.push(request.url());
        }
    });
    await dropCard(page);
    expect(mutations).toEqual([]);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(mutations).toEqual([]);
});

test("native app-owned cards cannot open cross-board selection", async ({ page }) => {
    await setup(page, false);
    const calls = await interceptAction(page, () => ({ status: 201, json: {} }));
    await dropCard(page, false);
    expect(calls.requests).toHaveLength(0);
});

for (const operation of ["Link card", "Clone card"]) {
    test(`ownership change before ${operation} prevents submission`, async ({ page }) => {
        await setup(page);
        const calls = await interceptAction(page, () => ({ status: 201, json: {} }));
        await dropCard(page);
        await page.evaluate(async () => {
            const moduleURL = "/src/core/stores/SocketStore.ts";
            const sockets = await import(moduleURL);
            const registryURL = "/src/core/stores/socket/registry.ts";
            const registry = await import(registryURL);
            const store = sockets.default.getState();
            for (const topic of Object.keys(store.getStore().subscribedTopicNotifiers)) {
                registry.subscribedCallback(topic, ["left"]);
            }
            const subscriptions = store.getStore().subscriptions;
            const callbacks = Object.values(subscriptions).flatMap((boards: any) =>
                Object.values(boards.left?.["board:card:details:changed:left-card"] ?? {}).flat()
            ) as Array<(data: unknown) => void>;
            if (!callbacks.length) throw new Error("Missing native card details subscription");
            for (const callback of callbacks) callback({ can_cross_board: false });
        });
        await page.getByRole("button", { name: operation, exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        expect(calls.requests).toHaveLength(0);
    });
}
