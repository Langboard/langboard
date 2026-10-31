import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page, canCrossBoard = true, location?: { moved: boolean; column?: string }) {
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
            if (location?.moved && path === `/board/${uid}/cards`) {
                const board = json as { cards: Record<string, unknown>[] };
                if (uid === "left") board.cards = [];
                if (uid === "right")
                    board.cards.push({
                        ...board.cards[0],
                        uid: "left-card",
                        project_column_uid: location.column ?? "right-column-0",
                        title: "left draggable card",
                    });
            }
            return route.fulfill({ json, headers });
        }
    );
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    await expect(page.getByText("left column 0", { exact: true }).first()).toBeVisible({ timeout: 45_000 });
    await expect(page.locator("[data-board-pane-footer]")).toHaveCount(0);
}

async function dropCard(page: Page, eligible = true, beforeDrop?: () => Promise<void>) {
    if (!(await page.locator("[data-workbench-explorer]").isVisible())) await page.getByRole("button", { name: "Explorer", exact: true }).click();
    await page.getByRole("button", { name: "Open right board in split view" }).first().click({ force: true });
    const panes = page.locator("[data-board-pane]");
    await expect(panes).toHaveCount(2);
    const source = panes.first().getByText("left draggable card", { exact: true });
    const destination = panes.nth(1).locator("[data-cross-board-column=right-column-0]");
    await expect(source).toBeVisible();
    await expect(destination).toBeVisible();
    await beforeDrop?.();
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
    await expect(page.getByRole("button", { name: "Move card", exact: true })).toBeVisible();
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
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(calls.requests).toHaveLength(0);
});

test("opening and cancelling native drop selection sends no mutation", async ({ page }) => {
    await setup(page);
    const mutations: string[] = [];
    page.on("request", (request) => {
        const path = new URL(request.url()).pathname;
        // Opening the destination records its native visit; the selector must not mutate board work.
        if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method()) && path.startsWith("/board/") && !/^\/board\/[^/]+\/visit$/.test(path)) {
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

for (const operation of ["Link card", "Clone card", "Move card"]) {
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
            const callbacks = Object.values(subscriptions).flatMap((boards) =>
                Object.values((boards as Record<string, Record<string, unknown>>).left?.["board:card:details:changed:left-card"] ?? {}).flat()
            ) as Array<(data: unknown) => void>;
            if (!callbacks.length) throw new Error("Missing native card details subscription");
            for (const callback of callbacks) callback({ can_cross_board: false });
        });
        await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(calls.requests).toHaveLength(0);
    });
}

const movedCard = { uid: "left-card", project_uid: "right", project_column_uid: "right-column-0" };
const excluded = { member_uids: ["private-member"], label_uids: ["private-label"] };

for (const first of ["left", "right"]) {
    test(`move retains same model and pane state when ${first} refresh completes first`, async ({ page }) => {
        const location = { moved: false };
        await setup(page, true, location);
        const calls = await interceptAction(page, () => {
            location.moved = true;
            return { status: 201, json: { card: movedCard, moved: true, excluded } };
        });
        await dropCard(page, true, async () => {
            const right = page.locator("[data-board-pane]").nth(1);
            await right.getByRole("button", { name: "Filters", exact: true }).click();
            await page.getByRole("textbox", { name: "Keyword", exact: true }).fill("draggable");
            await page.keyboard.press("Escape");
            await right.getByRole("button", { name: "Add a card", exact: true }).first().click();
            await right.getByPlaceholder("Enter a title", { exact: true }).fill("Retained destination draft");
        });
        const panes = page.locator("[data-board-pane]");
        const viewport = panes.nth(1).locator("[id^=board-scrollport-]").first();
        await viewport.evaluate((element) => {
            element.scrollLeft = 200;
        });
        await panes.evaluateAll((elements) => elements.forEach((element) => ((element as HTMLElement).dataset.retained = "true")));
        await page.evaluate(async () => {
            const url = "/src/core/models/ProjectCard.ts";
            const cards = await import(url);
            (window as unknown as { originalCard: unknown }).originalCard = cards.Model.getModel("left-card");
        });
        const refreshes: string[] = [];
        let release: (() => void) | undefined;
        const firstDone = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route(
            (url) => /^\/board\/[^/]+\/cards$/.test(url.pathname),
            async (route) => {
                if (route.request().method() !== "GET") return route.fallback();
                const board = new URL(route.request().url()).pathname.split("/")[2];
                refreshes.push(board);
                if (board !== first) await firstDone;
                await route.fallback();
                if (board === first) release?.();
            }
        );
        await page.getByRole("button", { name: "Move card", exact: true }).click();
        await expect(page.getByText("Cross-board action completed.", { exact: true })).toBeVisible();
        expect(refreshes).toContain("left");
        expect(refreshes).toContain("right");
        expect(calls.requests).toHaveLength(1);
        expect(calls.requests[0]).toMatchObject({ operation: "move", source_card_uid: "left-card", destination_column_uid: "right-column-0" });
        expect(calls.changes).toEqual([]);
        await expect(panes).toHaveCount(2);
        await expect(panes.first()).toHaveAttribute("data-retained", "true");
        await expect(panes.nth(1)).toHaveAttribute("data-retained", "true");
        await expect(panes.first().getByText("left draggable card", { exact: true })).toHaveCount(0);
        await expect(panes.nth(1).getByText("left draggable card", { exact: true })).toHaveCount(1);
        await expect(panes.nth(1).getByPlaceholder("Enter a title", { exact: true })).toHaveValue("Retained destination draft");
        await expect(panes.nth(1).getByRole("button", { name: "Filters", exact: true, includeHidden: true })).toHaveText("Filters (1)");
        await expect.poll(() => viewport.evaluate((element) => element.scrollLeft)).toBe(200);
        expect(
            await page.evaluate(async () => {
                const url = "/src/core/models/ProjectCard.ts";
                const cards = await import(url);
                const current = cards.Model.getModel("left-card");
                return { same: current === (window as unknown as { originalCard: unknown }).originalCard, project: current?.project_uid };
            })
        ).toEqual({ same: true, project: "right" });
        await expect(page.getByText("private-member", { exact: false })).toHaveCount(0);
    });
}

test("move refreshes audience token, requires operation-specific consent and preserves selected retry", async ({ page }) => {
    const location = { moved: false };
    await setup(page, true, location);
    const calls = await interceptAction(page, (_body, count) => {
        if (count < 3) return { status: 409, json: { confirmation_required: true, confirmation_token: `revision-${count}`, moved: false, excluded } };
        if (count === 3) return { status: 503, json: { title: "private-error" } };
        location.moved = true;
        return { status: 200, json: { card: movedCard, moved: false, excluded } };
    });
    await dropCard(page);
    await page.getByRole("button", { name: "Move card", exact: true }).click();
    await expect(page.getByText("Moving will expose this card to new readers. Continue?", { exact: true })).toBeVisible();
    expect(calls.requests).toHaveLength(1);
    await page.getByRole("button", { name: "Confirm exposure" }).click();
    await expect.poll(() => calls.requests.length).toBe(2);
    await expect(page.getByRole("button", { name: "Confirm exposure" })).toBeEnabled();
    await page.getByRole("button", { name: "Confirm exposure" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Clone card", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Link card", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Confirm exposure" }).click();
    await expect(page.getByText("Cross-board action completed.", { exact: true })).toBeVisible();
    expect(calls.requests.map((body) => body.confirmation_token)).toEqual([undefined, "revision-1", "revision-2", "revision-2"]);
    expect(new Set(calls.requests.map((body) => body.request_uid)).size).toBe(1);
    expect(calls.requests.every((body) => body.operation === "move")).toBe(true);
    await expect(page.getByText("private-error", { exact: false })).toHaveCount(0);
});

for (const invalid of ["identity", "project", "column", "exclusions", "flag", "conflict"]) {
    test(`move rejects malformed ${invalid} without model mutation or private payload`, async ({ page }) => {
        await setup(page);
        const card = { ...movedCard };
        if (invalid === "identity") card.uid = "different-card";
        if (invalid === "project") card.project_uid = "private-board";
        if (invalid === "column") card.project_column_uid = "private-column";
        const calls = await interceptAction(page, () => ({
            status: invalid === "conflict" ? 409 : 201,
            json: {
                card,
                moved: invalid === "flag" ? "true" : true,
                excluded: invalid === "exclusions" ? { member_uids: "private-payload", label_uids: [] } : excluded,
                title: "private-payload",
            },
        }));
        await dropCard(page);
        await page.getByRole("button", { name: "Move card", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByText("private-payload", { exact: false })).toHaveCount(0);
        await expect(page.getByText("Cross-board action completed.", { exact: true })).toHaveCount(0);
        expect(
            await page.evaluate(async () => {
                const url = "/src/core/models/ProjectCard.ts";
                return (await import(url)).Model.getModel("left-card")?.project_uid;
            })
        ).toBe("left");
        await page.getByRole("button", { name: "Cancel", exact: true }).click();
        expect(calls.requests).toHaveLength(1);
    });
}

for (const teardown of ["source", "session", "account", "pane"]) {
    test(`move ${teardown} teardown invalidates an in-flight successful result`, async ({ page }) => {
        await setup(page);
        let release: (() => void) | undefined;
        const wait = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route("**/board/right/card/cross-board", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fallback();
            await wait;
            await route
                .fulfill({
                    status: 201,
                    json: { card: movedCard, moved: true, excluded },
                    headers: { "access-control-allow-origin": "http://127.0.0.1:4197", "access-control-allow-credentials": "true" },
                })
                .catch(() => {});
        });
        await dropCard(page);
        const sent = page.waitForRequest((request) => request.method() === "POST" && request.url().includes("/card/cross-board"));
        await page.getByRole("button", { name: "Move card", exact: true }).click();
        await sent;
        if (teardown === "pane") {
            await page
                .getByRole("button", { name: "Close left board", exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
        } else {
            await page.evaluate(async (kind) => {
                if (kind === "source") {
                    const url = "/src/core/models/ProjectCard.ts";
                    (await import(url)).Model.deleteModel("left-card");
                } else {
                    const url = "/src/core/stores/AuthStore.ts";
                    const auth = (await import(url)).default;
                    if (kind === "account") auth.setState({ currentUser: null });
                    else {
                        const version = auth.getState().getSessionVersion() + 1;
                        auth.setState({ getSessionVersion: () => version });
                    }
                }
            }, teardown);
        }
        release?.();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(page.getByText("Cross-board action completed.", { exact: true })).toHaveCount(0);
    });
}

test("declining move audience exposure sends no mutation beyond preflight", async ({ page }) => {
    await setup(page);
    const calls = await interceptAction(page, () => ({
        status: 409,
        json: { confirmation_required: true, confirmation_token: "move-token", moved: false, excluded },
    }));
    await dropCard(page);
    await page.getByRole("button", { name: "Move card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Confirm exposure" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(calls.requests).toHaveLength(1);
});

test("source-first native socket refresh recovers same server card UID at destination", async ({ page }) => {
    const location = { moved: false };
    await setup(page, true, location);
    let release: (() => void) | undefined;
    const wait = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/board/right/card/cross-board", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fallback();
        await wait;
        await route
            .fulfill({
                status: 201,
                json: { card: movedCard, moved: true, excluded },
                headers: { "access-control-allow-origin": "http://127.0.0.1:4197", "access-control-allow-credentials": "true" },
            })
            .catch(() => {});
    });
    await dropCard(page);
    const sent = page.waitForRequest((request) => request.method() === "POST" && request.url().includes("/card/cross-board"));
    await page.getByRole("button", { name: "Move card", exact: true }).click();
    await sent;
    location.moved = true;
    const publish = async (uid: string) =>
        page.evaluate(async (boardUID) => {
            const url = "/src/core/stores/SocketStore.ts";
            const registryURL = "/src/core/stores/socket/registry.ts";
            const store = (await import(url)).default.getState();
            const registry = await import(registryURL);
            for (const topic of Object.keys(store.getStore().subscribedTopicNotifiers)) registry.subscribedCallback(topic, [boardUID]);
            const callbacks = Object.values(store.getStore().subscriptions).flatMap((boards) =>
                Object.values((boards as Record<string, Record<string, unknown>>)[boardUID]?.[`board:details:changed:${boardUID}`] ?? {}).flat()
            ) as Array<(data: unknown) => void>;
            if (!callbacks.length) throw new Error("Missing native board visibility subscription");
            for (const callback of callbacks) callback({ card_visibility_changed: true });
        }, uid);
    await publish("left");
    await expect(page.locator("[data-board-pane]").first().getByText("left draggable card", { exact: true })).toHaveCount(0);
    await publish("right");
    await expect(page.locator("[data-board-pane]").nth(1).getByText("left draggable card", { exact: true })).toBeVisible();
    release?.();
    expect(
        await page.evaluate(async () => {
            const url = "/src/core/models/ProjectCard.ts";
            const card = (await import(url)).Model.getModel("left-card");
            return { uid: card?.uid, project: card?.project_uid, column: card?.project_column_uid };
        })
    ).toEqual({ uid: "left-card", project: "right", column: "right-column-0" });
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
});

for (const column of ["right-column-0", "right-column-1"]) {
    test(`ambiguous initial move retries same operation and UUID in ${column} without alternate actions`, async ({ page }) => {
        const location = { moved: false, column };
        await setup(page, true, location);
        const calls = await interceptAction(page, (_body, count) => {
            if (count === 1) return { status: 503, json: {} };
            location.moved = true;
            return { status: 200, json: { card: { ...movedCard, project_column_uid: column }, moved: false, excluded } };
        });
        await dropCard(page);
        await page.getByRole("button", { name: "Move card", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByRole("button", { name: "Clone card", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Link card", exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "Move card", exact: true }).click();
        await expect(page.getByText("Cross-board action completed.", { exact: true })).toBeVisible();
        expect(calls.requests).toHaveLength(2);
        expect(calls.requests[0]).toEqual(calls.requests[1]);
        expect(
            await page.evaluate(async () => {
                const url = "/src/core/models/ProjectCard.ts";
                return (await import(url)).Model.getModel("left-card")?.project_column_uid;
            })
        ).toBe(column);
    });
}

async function retainedSnapshot(page: Page) {
    return page.evaluate(async () => {
        const url = "/src/pages/BoardPage/components/board/crossBoard/CrossBoardDrop.fixture.tsx";
        return (await import(url)).retainedCardSnapshot();
    });
}

async function installRetainedProbe(page: Page) {
    await page.evaluate(async () => {
        const url = "/src/pages/BoardPage/components/board/crossBoard/CrossBoardDrop.fixture.tsx";
        (await import(url)).installRetainedCardProbe("left-card");
    });
}

async function emitRetainedEvent(page: Page, project: string, event: string, data: unknown) {
    await page.evaluate(
        async ({ project, event, data }) => {
            const emit = (
                window as unknown as {
                    emitNativeSummaryEvent: (topic: string, uid: string, event: string, data: unknown) => Promise<void>;
                }
            ).emitNativeSummaryEvent;
            await emit("board", project, event, data);
        },
        { project, event, data }
    );
}

test("actual transfer retains identity and replaces board subscriptions through subsequent rereads and deletion", async ({ page }) => {
    const location = { moved: false };
    await setup(page, true, location);
    await interceptAction(page, () => {
        location.moved = true;
        return { status: 200, json: { card: movedCard, moved: true, excluded } };
    });
    await dropCard(page, true, () => installRetainedProbe(page));
    await emitRetainedEvent(page, "left", "board:card:details:changed:left-card", { title: "Initial construction subscribed" });
    expect(await retainedSnapshot(page)).toMatchObject({ same: true, title: "Initial construction subscribed", count: 1 });
    await page.getByRole("button", { name: "Move card", exact: true }).click();
    await expect(page.getByText("Cross-board action completed.", { exact: true })).toBeVisible();
    expect(await retainedSnapshot(page)).toMatchObject({ same: true, uid: "left-card", project: "right", column: "right-column-0", count: 1 });
    await emitRetainedEvent(page, "right", "board:card:details:changed:left-card", {
        title: "Destination owner change delivered",
        description: { content: "Destination detail" },
        can_cross_board: false,
    });
    expect(await retainedSnapshot(page)).toMatchObject({ same: true, title: "Destination owner change delivered", canCrossBoard: false, count: 1 });
    await emitRetainedEvent(page, "left", "board:card:details:changed:left-card", { title: "Stale old board", can_cross_board: true });
    await emitRetainedEvent(page, "left", "board:card:labels:updated:left-card", {
        labels: [
            { uid: "stale-label", project_uid: "left", name: "Stale", created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
        ],
    });
    await emitRetainedEvent(page, "left", "board:card:deleted:left-card", {});
    expect(await retainedSnapshot(page)).toMatchObject({
        same: true,
        title: "Destination owner change delivered",
        canCrossBoard: false,
        labels: [],
        count: 1,
    });
    const before = await retainedSnapshot(page);
    const rereadDeltas = await page.evaluate(async () => {
        const url = "/src/pages/BoardPage/components/board/crossBoard/CrossBoardDrop.fixture.tsx";
        const fixture = await import(url);
        return Array.from({ length: 3 }, () => fixture.rereadRetainedCard({ project_uid: "right", title: "Reread destination" }).notifierDelta);
    });
    const reread = await retainedSnapshot(page);
    expect(reread.callbacks).toEqual(before.callbacks);
    expect(rereadDeltas).toEqual(Array.from({ length: 3 }, () => ({ added: 0, removed: 0 })));
    expect(reread.callbacks.filter((item: { topic: string; scope: string }) => item.topic === "board" && item.scope === "left")).toEqual([]);
    expect(reread.callbacks.every((item: { count: number }) => item.count === 1)).toBe(true);
    await test
        .info()
        .attach("retained-transfer-subscriptions.json", { body: JSON.stringify({ before, reread }, null, 2), contentType: "application/json" });
    await emitRetainedEvent(page, "right", "board:card:details:changed:left-card", { title: "After reread event" });
    expect(await retainedSnapshot(page)).toMatchObject({ same: true, title: "After reread event" });
    await emitRetainedEvent(page, "right", "board:card:deleted:left-card", {});
    await expect.poll(async () => (await retainedSnapshot(page)).count).toBe(0);
    await expect.poll(async () => (await retainedSnapshot(page)).callbacks.length).toBe(0);
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
});

test("retained model reread changes board and mirror handler families without duplicate subscriptions", async ({ page }) => {
    await setup(page);
    await installRetainedProbe(page);
    const initial = await retainedSnapshot(page);
    const reread = (update: Record<string, unknown>) =>
        page.evaluate(async (fields) => {
            const url = "/src/pages/BoardPage/components/board/crossBoard/CrossBoardDrop.fixture.tsx";
            return (await import(url)).rereadRetainedCard(fields);
        }, update);
    expect((await reread({ project_uid: "right" })).notifierDelta).toEqual({ added: 3, removed: 3 });
    // Destination activation uses the native topic registry, as a destination pane does.
    await page.evaluate(async () => {
        const url = "/src/core/stores/SocketStore.ts";
        (await import(url)).default.getState().subscribe("board" as never, ["right"]);
    });
    await emitRetainedEvent(page, "right", "board:card:details:changed:left-card", { title: "Reread board subscribed" });
    expect(await retainedSnapshot(page)).toMatchObject({ same: true, title: "Reread board subscribed", count: 1 });
    for (const source_type of ["project_card", "project_wiki"]) {
        const state = await reread({
            source_type,
            linked_resource: { type: source_type, uid: "original", status: "available", title: "Read-only source" },
        });
        expect(state.callbacks.some((item: { event: string }) => item.event === "board:card:details:changed:left-card")).toBe(false);
        expect(
            state.callbacks.some(
                (item: { event: string; key: string }) => item.event === "metadata:updated:left-card" && item.key.startsWith("metadata-")
            )
        ).toBe(false);
        expect(state.notifierDelta).toEqual({ added: 2, removed: source_type === "project_card" ? 3 : 2 });
        await emitRetainedEvent(page, "right", "board:card:details:changed:left-card", { title: "Forbidden task edit", can_cross_board: true });
        expect(await retainedSnapshot(page)).toMatchObject({ same: true, title: "Read-only source", sourceType: source_type, count: 1 });
    }
    expect((await reread({ source_type: undefined, title: "Independent task" })).notifierDelta).toEqual({ added: 3, removed: 2 });
    await emitRetainedEvent(page, "right", "board:card:details:changed:left-card", { title: "Task subscription restored", can_cross_board: false });
    expect(await retainedSnapshot(page)).toMatchObject({
        same: true,
        title: "Task subscription restored",
        canCrossBoard: false,
        sourceType: null,
        count: 1,
    });
    const restored = await retainedSnapshot(page);
    const deleted = await page.evaluate(async () => {
        const url = "/src/pages/BoardPage/components/board/crossBoard/CrossBoardDrop.fixture.tsx";
        return (await import(url)).deleteRetainedCard();
    });
    expect(deleted.notifierDelta).toEqual({ added: 0, removed: 3 });
    await expect.poll(async () => (await retainedSnapshot(page)).callbacks.length).toBe(0);
    const receipt = JSON.stringify({ initial, restored, deleted, afterCleanup: await retainedSnapshot(page) }, null, 2);
    await test.info().attach("retained-reread-subscriptions.json", { body: receipt, contentType: "application/json" });
});
