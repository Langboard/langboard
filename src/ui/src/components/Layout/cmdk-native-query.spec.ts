import { expect, test, type Page, type Route, type TestInfo, type WebSocketRoute } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

const fixtureURL = "/src/components/Layout/cmdk-native-query.fixture.html";
const palette = (page: Page) => page.getByRole("dialog");

async function queryReceipt(page: Page) {
    return page.evaluate(async () => {
        const queryPath = "/src/core/helpers/QueryClient.ts";
        const authPath = "/src/core/stores/AuthStore.ts";
        const modelPath = "/src/core/models/index.ts";
        const [{ queryClient }, { default: auth }, { Project }] = await Promise.all([
            import(/* @vite-ignore */ queryPath),
            import(/* @vite-ignore */ authPath),
            import(/* @vite-ignore */ modelPath),
        ]);
        return {
            defaults: queryClient.getDefaultOptions(),
            actor: auth.getState().currentUser?.uid,
            projects: Project.Model.getModels(() => true).map((project: { uid: string; title: string }) => ({
                uid: project.uid,
                title: project.title,
            })),
            queries: queryClient
                .getQueryCache()
                .findAll({ queryKey: ["get-dashboard-projects"] })
                .map(
                    (query: {
                        queryKey: unknown[];
                        state: { fetchStatus: string; status: string; isInvalidated: boolean; dataUpdateCount: number };
                        options: { staleTime: number };
                        isActive: () => boolean;
                    }) => ({
                        key: query.queryKey,
                        fetchStatus: query.state.fetchStatus,
                        status: query.state.status,
                        isInvalidated: query.state.isInvalidated,
                        dataUpdateCount: query.state.dataUpdateCount,
                        staleTime: query.options.staleTime,
                        active: query.isActive(),
                    })
                ),
        };
    });
}

async function updateSession(page: Page, token: string) {
    await page.evaluate(async (nextToken) => {
        const authPath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: auth }, { api }] = await Promise.all([import(/* @vite-ignore */ authPath), import(/* @vite-ignore */ apiPath)]);
        await auth.getState().updateToken(nextToken, api);
    }, token);
}

async function nativeFixture(page: Page, testInfo: TestInfo) {
    const now = new Date().toISOString();
    let actor = "actor-a";
    let revision = 1;
    let hold = false;
    let acknowledge = true;
    let peer: WebSocketRoute | undefined;
    const held: { route: Route; json: unknown }[] = [];
    const pendingAcks: { socket: WebSocketRoute; message: string }[] = [];
    const reads: { actor: string; revision: number; startMs: number }[] = [];
    const wire: { direction: string; payload: unknown; atMs: number }[] = [];
    const stages: { label: string; reads: number; query: unknown }[] = [];
    const errors: string[] = [];
    const http: { method: string; path: string }[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.routeWebSocket(/.*/, (socket) => {
        peer = socket;
        socket.onMessage((raw) => {
            const message = JSON.parse(String(raw));
            wire.push({ direction: "client", payload: message, atMs: Date.now() });
            if (!["subscribe", "unsubscribe"].includes(message.event)) return;
            const ack = { ...message, event: message.event === "subscribe" ? "subscribed" : "unsubscribed" };
            if (message.topic === "dashboard" && !acknowledge) pendingAcks.push({ socket, message: JSON.stringify(ack) });
            else {
                wire.push({ direction: "server", payload: ack, atMs: Date.now() });
                socket.send(JSON.stringify(ack));
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
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const path = new URL(route.request().url()).pathname;
            const method = route.request().method();
            http.push({ method, path });
            if (method === "OPTIONS") return route.fulfill({ status: 204 });
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture-session-1", user: user(), bots: [] };
            else if (path === "/dashboard/projects") {
                reads.push({ actor, revision, startMs: Date.now() });
                json = {
                    projects: [
                        {
                            uid: `${actor}-project`,
                            title: `${actor} revision ${revision}`,
                            owner_uid: actor,
                            project_type: "default",
                            created_at: now,
                            updated_at: now,
                            all_members: [user()],
                            invited_member_uids: [],
                            internal_bots: [],
                            current_auth_role_actions: ["*"],
                            labels: [],
                            description: "",
                            archive_visible_days: 30,
                        },
                    ],
                    columns: [],
                };
                if (hold) {
                    held.push({ route, json });
                    return;
                }
            } else if (path === "/account/project-collections") json = { revision: 0, collections: [] };
            else if (path.includes("/search")) json = { items: [], next_cursor: null };
            await route.fulfill({ json });
        }
    );
    return {
        reads,
        held,
        pendingAcks,
        errors,
        stages,
        http,
        setActor: (value: string) => (actor = value),
        setRevision: (value: number) => (revision = value),
        hold: () => (hold = true),
        release: async (status = 200) => {
            hold = false;
            for (const pending of held.splice(0)) await pending.route.fulfill({ status, json: pending.json });
        },
        holdAcks: () => (acknowledge = false),
        acknowledge: () => {
            acknowledge = true;
            for (const pending of pendingAcks.splice(0)) {
                wire.push({ direction: "server", payload: JSON.parse(pending.message), atMs: Date.now() });
                pending.socket.send(pending.message);
            }
        },
        reconnect: () => peer!.close({ code: 1012, reason: "Labelled reconnect" }),
        emit: () => {
            const message = {
                topic: "dashboard",
                topic_id: `${actor}-project`,
                event: `dashboard:project:activity:recorded:${actor}-project`,
                data: {},
            };
            wire.push({ direction: "server", payload: message, atMs: Date.now() });
            peer!.send(JSON.stringify(message));
        },
        settle: async () => {
            await expect
                .poll(async () => (await queryReceipt(page)).queries.every((query: { fetchStatus: string }) => query.fetchStatus === "idle"))
                .toBe(true);
            await page.waitForTimeout(550);
        },
        record: async (label: string) => stages.push({ label, reads: reads.length, query: await queryReceipt(page) }),
        save: async () => {
            const directory = "../../.verification/cmdk-project-fetch";
            await mkdir(directory, { recursive: true });
            const name = testInfo.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
            const path = `${directory}/${name}.json`;
            await writeFile(
                path,
                JSON.stringify(
                    {
                        scope: "Native QueryClient, API, AuthStore, SocketProvider, palette and Explorer; labelled HTTP/WebSocket fixtures",
                        limitations: [
                            "Not production or authenticated E2E",
                            "Fixture request latency does not reproduce backend 606ms/5988ms timing",
                        ],
                        reads,
                        wire,
                        stages,
                        http,
                        errors,
                    },
                    null,
                    2
                )
            );
            await testInfo.attach("native-palette-lifecycle", { path, contentType: "application/json" });
        },
    };
}

test("cold open catches changes between initial snapshot and subscription acknowledgement", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        fixture.holdAcks();
        await page.goto(fixtureURL);
        await expect(page.getByRole("button", { name: "Open palette" })).toBeVisible();
        expect(fixture.reads).toHaveLength(0);
        await page.getByRole("button", { name: "Open palette" }).click();
        await expect(palette(page).getByRole("option", { name: /actor-a revision 1/ })).toBeVisible();
        await expect.poll(() => fixture.pendingAcks.length).toBeGreaterThan(0);
        await fixture.settle();
        await fixture.record("snapshot before acknowledgement");
        expect(fixture.reads).toHaveLength(1);
        fixture.setRevision(2);
        fixture.acknowledge();
        await expect(palette(page).getByRole("option", { name: /actor-a revision 2/ })).toBeVisible();
        await fixture.settle();
        await fixture.record("acknowledgement closes subscription gap");
        expect(fixture.reads).toHaveLength(2);
        expect((await queryReceipt(page)).defaults).toEqual({});
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

test("warm Explorer palette opens reuse the authorized snapshot", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await page.goto(`${fixtureURL}?explorer=1`);
        await expect(page.getByRole("navigation", { name: "Explorer" })).toContainText("actor-a revision 1");
        await fixture.settle();
        await fixture.record("loaded Explorer");
        for (let opening = 0; opening < 2; opening++) {
            await page.getByRole("button", { name: "Open palette" }).click();
            await expect(palette(page).getByRole("option", { name: /actor-a revision 1/ })).toBeVisible();
            await fixture.settle();
            await fixture.record(`palette opening ${opening + 1}`);
            await page.keyboard.press("Escape");
        }
        expect(fixture.stages.map(({ reads }) => reads)).toEqual([2, 2, 2]);
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

test("stale opening activity bursts and reconnect each refresh the authorized snapshot", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await page.goto(`${fixtureURL}?explorer=1`);
        await expect(page.getByRole("navigation", { name: "Explorer" })).toContainText("actor-a revision 1");
        await fixture.settle();
        expect(fixture.reads).toHaveLength(2);
        fixture.setRevision(2);
        // Advance only wall-clock staleness; native timers and HTTP remain live.
        await page.clock.setFixedTime(new Date(Date.now() + 31_000));
        await page.getByRole("button", { name: "Open palette" }).click();
        await expect(palette(page).getByRole("option", { name: /actor-a revision 2/ })).toBeVisible();
        await fixture.settle();
        await fixture.record("stale palette opening");
        expect(fixture.reads).toHaveLength(3);

        fixture.setRevision(3);
        for (let event = 0; event < 10; event++) fixture.emit();
        await expect(palette(page).getByRole("option", { name: /actor-a revision 3/ })).toBeVisible();
        await fixture.settle();
        await fixture.record("ten activity events");
        expect(fixture.reads).toHaveLength(4);

        fixture.holdAcks();
        fixture.setRevision(4);
        fixture.reconnect();
        await expect.poll(() => fixture.pendingAcks.length, { timeout: 10_000 }).toBeGreaterThan(0);
        expect(fixture.reads).toHaveLength(4);
        fixture.acknowledge();
        await expect(palette(page).getByRole("option", { name: /actor-a revision 4/ })).toBeVisible();
        await fixture.settle();
        await fixture.record("reconnect after acknowledgement");
        expect(fixture.reads).toHaveLength(5);
        expect((await queryReceipt(page)).defaults).toEqual({});
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

test("actor replacement discards the previous actor pending snapshot", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        fixture.hold();
        await page.goto(fixtureURL);
        await page.getByRole("button", { name: "Open palette" }).click();
        await expect.poll(() => fixture.held.length).toBe(1);
        fixture.setActor("actor-b");
        fixture.setRevision(2);
        await updateSession(page, "fixture-session-2");
        await expect.poll(() => fixture.held.length).toBe(2);
        await fixture.release();
        await expect(palette(page).getByRole("option", { name: /actor-b revision 2/ })).toBeVisible();
        await fixture.settle();
        await fixture.record("actor replacement");
        expect(fixture.reads.map(({ actor }) => actor)).toEqual(["actor-a", "actor-b", "actor-b"]);
        const state = await queryReceipt(page);
        expect(state.actor).toBe("actor-b");
        expect(state.projects.map((project: { uid: string }) => project.uid)).toEqual(["actor-b-project"]);
        expect(state.queries.map((query: { key: string[] }) => query.key)).toEqual([["get-dashboard-projects", "actor-b"]]);
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

for (const previousStatus of [200, 401]) {
    test(`session renewal rejects previous session ${previousStatus} response and permits a fresh opening`, async ({ page }, testInfo) => {
        const fixture = await nativeFixture(page, testInfo);
        try {
            fixture.hold();
            await page.goto(fixtureURL);
            await page.getByRole("button", { name: "Open palette" }).click();
            await expect.poll(() => fixture.held.length).toBe(1);
            await updateSession(page, "fixture-session-2");
            await fixture.release(previousStatus);
            await expect.poll(async () => (await queryReceipt(page)).queries[0]?.status).toBe("error");
            await fixture.record("rejected previous session response");
            const state = await queryReceipt(page);
            expect(state.actor).toBe("actor-a");
            expect(state.projects).toEqual([]);
            expect(fixture.http.filter(({ path }) => path === "/auth/refresh")).toHaveLength(0);
            fixture.setRevision(2);
            await page.keyboard.press("Escape");
            await page.getByRole("button", { name: "Open palette" }).click();
            await expect(palette(page).getByRole("option", { name: /actor-a revision 2/ })).toBeVisible();
            await fixture.settle();
            await fixture.record("fresh opening after renewal");
            expect(fixture.reads.map(({ revision }) => revision)).toEqual([1, 2, 2]);
            expect(fixture.errors).toEqual([]);
        } finally {
            await fixture.save();
        }
    });
}
