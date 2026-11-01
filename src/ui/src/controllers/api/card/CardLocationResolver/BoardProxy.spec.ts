import { test, expect, type Page } from "@playwright/test";

const source = "Source00001";
const target = "Target00001";
const card = "Card0000001";
async function setup(
    page: Page,
    options: {
        entry?: string;
        status?: number;
        payload?: unknown;
        wait?: Promise<void>;
        split?: boolean;
        cycle?: boolean;
        isMoved?: () => boolean;
    } = {}
) {
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
    const projects = [source, target, "Other000001"].map((uid) => ({
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
    const calls: string[] = [];
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const url = new URL(route.request().url());
            const path = url.pathname;
            const origin = route.request().headers().origin || "http://127.0.0.1:4196";
            const headers = {
                "access-control-allow-origin": origin,
                "access-control-allow-credentials": "true",
                "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                "access-control-allow-headers": "authorization,content-type,content-encoding",
            };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            calls.push(path + url.search);
            const uid = path.split("/")[2];
            if (url.searchParams.has("location_only")) {
                if (uid === source) await options.wait;
                if (options.status === 0) return route.abort("failed");
                return route.fulfill({
                    status: options.status ?? 200,
                    headers,
                    json: options.payload ?? {
                        canonical_project_uid:
                            uid === source ? (options.isMoved && !options.isMoved() ? source : target) : options.cycle ? source : uid,
                        card_uid: card,
                    },
                });
            }
            if (path === `/board/${source}` && !options.isMoved)
                return route.fulfill({ status: 403, headers, json: { title: "private-source-title" } });
            if (path === `/board/${source}/card/${card}` && options.isMoved?.()) {
                return route.fulfill({ headers, json: { canonical_project_uid: target, card_uid: card } });
            }
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user, bots: [] };
            else if (path === `/board/${uid}`)
                json = { project: projects.find((project) => project.uid === uid), project_bot_scopes: [], project_bot_schedules: [] };
            else if (path === `/board/${uid}/cards`)
                json = {
                    cards: [
                        {
                            uid: card,
                            project_uid: uid,
                            project_column_uid: `${uid}-column-0`,
                            project_column_name: `${uid} column 0`,
                            title: `${uid} draggable card`,
                            created_at: now,
                            updated_at: now,
                            order: 0,
                            can_cross_board: true,
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
            else if (path === `/board/${uid}/card/${card}`)
                json = {
                    card: {
                        uid: card,
                        project_uid: uid,
                        project_column_uid: `${uid}-column-0`,
                        title: "authorized destination card",
                        created_at: now,
                        updated_at: now,
                        member_uids: [],
                        project_members: [],
                        labels: [],
                        relationships: [],
                        current_auth_role_actions: ["*"],
                        has_description: false,
                        count_comment: 0,
                        order: 0,
                        description: "",
                    },
                    checklists: [],
                    attachments: [],
                    linked_wikis: [],
                    global_relationships: [],
                    project_columns: [],
                    project_labels: [],
                    bot_scopes: [],
                    execution_receipts: [],
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
    const entry = options.entry ?? `/board/${source}/${card}?filters=keyword:kept#anchor`;
    const fixture = "/src/controllers/api/card/CardLocationResolver/BoardProxy.fixture.html";
    await page.goto(`${fixture}?entry=${encodeURIComponent(entry)}${options.split ? "&split=1" : ""}`);
    return calls;
}

test("target-only reader resolves old URL before denied source board fetch; preserves query and hash", async ({ page }) => {
    const calls = await setup(page);
    await expect(page.locator("[data-native-location]")).toHaveText(`/board/${target}/${card}?filters=keyword:kept#anchor`, { timeout: 45000 });
    await expect.poll(() => calls.includes(`/board/${target}?include_automation=false`)).toBe(true);
    expect(calls.some((path) => path === `/board/${source}?include_automation=false`)).toBe(false);
    expect(calls.indexOf(`/board/${target}/card/${card}?location_only=true`)).toBeLessThan(
        calls.indexOf(`/board/${target}?include_automation=false`)
    );
    await expect(page.getByRole("dialog", { name: `${target} draggable card`, exact: true })).toBeVisible();
    await expect(page.getByText("private-source-title")).toHaveCount(0);
});
for (const status of [403, 404, 503])
    test(`native ${status} location denial shows retry without source load or payload`, async ({ page }) => {
        const calls = await setup(page, { status, payload: { canonical_project_uid: target, card_uid: card, title: "private-source-title" } });
        await expect(page.getByRole("alert")).toBeVisible({ timeout: 45000 });
        await expect(page.locator("[data-native-location]")).toHaveText(`/board/${source}/${card}?filters=keyword:kept#anchor`);
        expect(calls.some((path) => /^\/board\/[^/]+\?/.test(path))).toBe(false);
        await expect(page.getByText("private-source-title")).toHaveCount(0);
        const before = calls.length;
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect.poll(() => calls.length).toBeGreaterThan(before);
    });
for (const [name, payload] of Object.entries({
    mismatched: { canonical_project_uid: target, card_uid: "OtherCard01" },
    url: { canonical_project_uid: "https://evil.example", card_uid: card },
    extra: { canonical_project_uid: target, card_uid: card, card: { title: "private-source-title" } },
    malformed: { canonical_project_uid: target },
}))
    test(`${name} response cannot redirect or load models`, async ({ page }) => {
        const calls = await setup(page, { payload });
        await expect(page.getByRole("alert")).toBeVisible({ timeout: 45000 });
        expect(calls.some((path) => /^\/board\/[^/]+\?/.test(path))).toBe(false);
        await expect(page.locator("[data-native-location]")).toHaveText(`/board/${source}/${card}?filters=keyword:kept#anchor`);
        const modelCount = await page.evaluate(async () => {
            const path = "/src/core/models/ProjectCard.ts";
            const cards = await import(path);
            return !!cards.Model.getModel("Card0000001");
        });
        expect(modelCount).toBe(false);
    });
test("current card URL completes preflight before ordinary board load", async ({ page }) => {
    const calls = await setup(page, { entry: `/board/${target}/${card}` });
    await expect.poll(() => calls.includes(`/board/${target}?include_automation=false`), { timeout: 45000 }).toBe(true);
    expect(calls.filter((path) => path.endsWith("location_only=true"))).toHaveLength(1);
    await expect(page.locator("[data-native-location]")).toHaveText(`/board/${target}/${card}`);
});
for (const suffix of ["", "/wiki", "/settings", "/cloud", "/graph"])
    test(`non-card route ${suffix || "board"} bypasses location lookup`, async ({ page }) => {
        const calls = await setup(page, { entry: `/board/${target}${suffix}` });
        await expect.poll(() => calls.includes(`/board/${target}?include_automation=false`), { timeout: 45000 }).toBe(true);
        expect(calls.some((path) => path.includes("location_only"))).toBe(false);
    });
test("navigation cancels stale old-route response", async ({ page }) => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
        release = resolve;
    });
    const calls = await setup(page, { wait });
    await expect.poll(() => calls.some((path) => path.includes("location_only")), { timeout: 45000 }).toBe(true);
    await page.evaluate(() => {
        history.pushState(null, "", "/board/Other000001");
        dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect.poll(() => calls.includes("/board/Other000001?include_automation=false")).toBe(true);
    release();
    await expect(page.locator("[data-native-location]")).toHaveText("/board/Other000001");
    expect(calls.includes(`/board/${target}?include_automation=false`)).toBe(false);
});
test("account change cancels old actor result", async ({ page }) => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
        release = resolve;
    });
    const calls = await setup(page, { wait });
    await expect.poll(() => calls.some((path) => path.includes("location_only")), { timeout: 45000 }).toBe(true);
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const auth = await import(path);
        auth.default.getState().removeToken();
    });
    release();
    await expect
        .poll(async () =>
            page.evaluate(async () => {
                const path = "/src/core/stores/AuthStore.ts";
                return (await import(path)).default.getState().currentUser;
            })
        )
        .toBeNull();
    await expect(page.locator("[data-native-location]")).toHaveText("/auth/signin");
    expect(calls.includes(`/board/${target}?include_automation=false`)).toBe(false);
});

test("background split canonicalization stays in its pane and preserves native focused board", async ({ page }) => {
    const calls = await setup(page, { entry: "/board/Other000001", split: true });
    await page.getByRole("button", { name: "Open fixture pane", exact: true }).click();
    const panes = page.locator("[data-fixture-pane]");
    await expect(panes).toHaveCount(2);
    await panes.nth(1).getByRole("button", { name: "Load old card", exact: true }).click();
    await expect(panes.nth(1).locator("[data-pane-location]")).toHaveText(`/board/${target}/${card}?filters=keyword:pane#pane`);
    await expect(panes.first().locator("[data-pane-location]")).toHaveText("/board/Other000001");
    await expect(page.locator("[data-native-location]")).toHaveText("/board/Other000001");
    expect(calls.some((path) => path === `/board/${source}?include_automation=false`)).toBe(false);
    expect(calls.filter((path) => path === `/board/${source}/card/${card}?location_only=true`)).toHaveLength(1);
});

test("closing a split pane cancels its pending location and cannot navigate survivor", async ({ page }) => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
        release = resolve;
    });
    const calls = await setup(page, { entry: "/board/Other000001", split: true, wait });
    await page.getByRole("button", { name: "Open fixture pane", exact: true }).click();
    const panes = page.locator("[data-fixture-pane]");
    await panes.nth(1).getByRole("button", { name: "Load old card", exact: true }).click();
    await expect.poll(() => calls.includes(`/board/${source}/card/${card}?location_only=true`)).toBe(true);
    await panes.nth(1).getByRole("button", { name: "Close fixture pane", exact: true }).click();
    release();
    await expect(panes).toHaveCount(1);
    await expect(page.locator("[data-native-location]")).toHaveText("/board/Other000001");
    await expect(panes.first().locator("[data-pane-location]")).toHaveText("/board/Other000001");
    expect(calls.filter((path) => path === `/board/${target}/card/${card}?location_only=true`)).toHaveLength(0);
});

test("network failure exposes existing retry without board fetch", async ({ page }) => {
    const calls = await setup(page, { status: 0 });
    await expect(page.getByRole("alert")).toBeVisible({ timeout: 45000 });
    await expect(page.locator("[data-native-location]")).toHaveText(`/board/${source}/${card}?filters=keyword:kept#anchor`);
    expect(calls.some((path) => /^\/board\/[^/]+\?/.test(path))).toBe(false);
});

test("contradicting canonical locations stop at retry instead of redirecting forever", async ({ page }) => {
    const calls = await setup(page, { cycle: true });
    await expect(page.getByRole("alert")).toBeVisible({ timeout: 45000 });
    await expect(page.locator("[data-native-location]")).toHaveText(`/board/${target}/${card}?filters=keyword:kept#anchor`);
    expect(calls.filter((path) => path.includes("location_only"))).toHaveLength(2);
    expect(calls.some((path) => /^\/board\/[^/]+\?/.test(path))).toBe(false);
});

test("native visibility socket re-resolves an already open moved card without hydrating minimal details", async ({ page }) => {
    let moved = false;
    const calls = await setup(page, { isMoved: () => moved });
    await expect(page.getByRole("dialog", { name: `${source} draggable card`, exact: true })).toBeVisible({ timeout: 45000 });
    moved = true;
    await page.evaluate(async () => {
        const socketPath = "/src/core/stores/SocketStore.ts";
        const registryPath = "/src/core/stores/socket/registry.ts";
        const sockets = await import(socketPath);
        const registry = await import(registryPath);
        const store = sockets.default.getState();
        for (const topic of Object.keys(store.getStore().subscribedTopicNotifiers)) registry.subscribedCallback(topic, ["Source00001"]);
        const subscriptions = store.getStore().subscriptions;
        const callbacks = Object.values(subscriptions).flatMap((boards: unknown) => {
            const scoped = boards as Record<string, Record<string, Record<string, Array<(data: unknown) => void>>>>;
            return Object.values(scoped.Source00001?.["board:details:changed:Source00001"] ?? {}).flat();
        });
        if (!callbacks.length) throw new Error("Native board visibility subscription missing");
        callbacks.forEach((callback) => callback({ card_visibility_changed: true }));
    });
    await expect(page.locator("[data-native-location]")).toHaveText(`/board/${target}/${card}?filters=keyword:kept#anchor`);
    await expect(page.getByRole("dialog", { name: `${target} draggable card`, exact: true })).toBeVisible();
    expect(calls.filter((path) => path === `/board/${source}/card/${card}?location_only=true`)).toHaveLength(2);
    const modelProject = await page.evaluate(async () => {
        const path = "/src/core/models/ProjectCard.ts";
        return (await import(path)).Model.getModel("Card0000001")?.project_uid;
    });
    expect(modelProject).toBe(target);
});
