import { expect, test, type Page, type Route, type TestInfo, type WebSocketRoute } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const fixtureURL = "/src/components/Layout/change-counts.fixture.html";
const sidebar = (page: Page) => page.locator("section[aria-label='Changes']");
const rail = (page: Page) => page.getByRole("button", { name: "Changes", exact: true });
const row = (page: Page, title = "Exact card") => sidebar(page).getByRole("button", { name: new RegExp(title) });

async function modelReceipt(page: Page) {
    return page.evaluate(async () => {
        const path = "/src/core/models/ProjectCard.ts";
        const { Model } = await import(/* @vite-ignore */ path);
        const card = Model.getModel("left-card");
        return card
            ? {
                  unread: card.has_unread_change,
                  count: card.unread_change_count,
                  status: card.unread_change_count_status,
                  sequence: card.last_change_seq,
                  target: card.last_change_target_uid,
                  readVersion: card.read_state_version,
              }
            : null;
    });
}

async function updateSession(page: Page, token: string) {
    await page.evaluate(async (value) => {
        const storePath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ storePath), import(/* @vite-ignore */ apiPath)]);
        await store.getState().updateToken(value, api);
    }, token);
}

async function nativeFixture(page: Page, testInfo: TestInfo) {
    const now = new Date().toISOString();
    let actor = "fixture-user";
    let hold = false;
    const held: { route: Route; json: unknown }[] = [];
    const heldSummaries: { route: Route; json: unknown }[] = [];
    const summaryReads: string[] = [];
    const pendingSeen: Route[] = [];
    const reads: { actor: string; token?: string }[] = [];
    const wire: unknown[] = [];
    const errors: string[] = [];
    let readerReads = 0;
    let peer: WebSocketRoute | undefined;
    const card = (uid: string, title: string, extra: Record<string, unknown>): Record<string, unknown> => ({
        uid,
        project_uid: "left",
        project_column_uid: "left-column",
        title,
        created_at: now,
        updated_at: now,
        order: 0,
        member_uids: [],
        labels: [],
        relationships: [],
        current_auth_role_actions: ["*"],
        has_unread_change: true,
        last_change_seq: 100,
        last_change_at: now,
        ...extra,
    });
    const cards = [
        card("left-card", "Exact card", {
            unread_change_count: 3,
            unread_change_count_status: "exact",
            last_change_target_type: "comment",
            last_change_target_uid: "latest-comment",
        }),
        card("unknown", "Historical card", { unread_change_count: null, unread_change_count_status: "unknown_history" }),
        card("manual", "Manual card", { unread_change_count: null, unread_change_count_status: "manual_unread" }),
        card("legacy", "Legacy card", {}),
        card("read", "Read card", { unread_change_count: 0, unread_change_count_status: "exact", has_unread_change: false }),
        card("mirror", "Mirror card", {
            unread_change_count: null,
            unread_change_count_status: "not_applicable",
            has_unread_change: false,
            source_type: "project_card",
            source_uid: "source-card",
        }),
    ];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.routeWebSocket(/.*/, (socket) => {
        peer = socket;
        socket.onMessage((raw) => {
            const message = JSON.parse(String(raw));
            if (["subscribe", "unsubscribe"].includes(message.event)) {
                socket.send(JSON.stringify({ ...message, event: message.event === "subscribe" ? "subscribed" : "unsubscribed" }));
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
        preferred_lang: "en-US",
        notification_unsubs: {},
        industry: "",
        purpose: "",
    });
    const project = () => ({
        uid: "left",
        title: "Left board",
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
            const path = new URL(route.request().url()).pathname;
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture-user-session", user: user(), bots: [] };
            else if (path === "/board/left") json = { project: project() };
            else if (path === "/board/left/cards") {
                reads.push({ actor, token: route.request().headers().authorization });
                json = structuredClone({
                    cards,
                    columns: [{ uid: "left-column", project_uid: "left", name: "Column", order: 0, created_at: now, updated_at: now }],
                    checklists: [],
                    global_relationships: [],
                });
                if (hold) {
                    held.push({ route, json });
                    return;
                }
            } else if (path === "/board/left/unread-card-summaries") {
                summaryReads.push(path);
                json = structuredClone({
                    items: cards
                        .filter((card) => card.has_unread_change && !card.source_type)
                        .map((card) => ({
                            uid: card.uid,
                            project_uid: card.project_uid,
                            title: card.title,
                            has_unread_change: true,
                            unread_change_count: card.unread_change_count ?? null,
                            unread_change_count_status: card.unread_change_count_status ?? "unknown_history",
                            latest_change: {
                                seq: card.last_change_seq,
                                target_type: card.last_change_target_type ?? null,
                                target_uid: card.last_change_target_uid ?? null,
                                at: card.last_change_at,
                            },
                        })),
                    next_cursor: null,
                    limit: 50,
                });
                if (hold) {
                    heldSummaries.push({ route, json });
                    return;
                }
            } else if (path === "/board/left/card/left-card/seen") {
                pendingSeen.push(route);
                return;
            } else if (path.endsWith("/read-state")) {
                readerReads++;
                json = { readers: [] };
            } else if (path.startsWith("/metadata/")) json = { metadata: {} };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project")) json = { projects: [project()], columns: [], records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            await route.fulfill({ json });
        }
    );
    return {
        cards,
        reads,
        summaryReads,
        held,
        pendingSeen,
        errors,
        readerReads: () => readerReads,
        setActor: (value: string) => (actor = value),
        hold: () => (hold = true),
        release: async () => {
            hold = false;
            for (const pending of [...held.splice(0), ...heldSummaries.splice(0)]) await pending.route.fulfill({ json: pending.json });
        },
        replySeen: async (json: unknown, index = 0) => pendingSeen.splice(index, 1)[0].fulfill({ json }),
        emit: (data: Record<string, unknown>, board = "left") => {
            const message = { topic: "board", topic_id: board, event: "board:card:details:changed:left-card", data };
            wire.push(message);
            peer!.send(JSON.stringify(message));
        },
        settle: async () => {
            await expect
                .poll(() =>
                    page.evaluate(async () => {
                        const path = "/src/core/helpers/QueryClient.ts";
                        const { queryClient } = await import(/* @vite-ignore */ path);
                        return queryClient.isFetching();
                    })
                )
                .toBe(0);
            await page.waitForTimeout(150);
        },
        save: async () => {
            const path = testInfo.outputPath("native-counts.json");
            await writeFile(
                path,
                JSON.stringify(
                    {
                        scope: "Local labelled HTTP/WebSocket fixtures with native client boundaries; not live E2E",
                        reads,
                        wire,
                        readerReads,
                        summaryReads,
                        errors,
                    },
                    null,
                    2
                )
            );
            await testInfo.attach("native-counts", { path, contentType: "application/json" });
        },
    };
}

async function openChanges(page: Page) {
    await page.goto(fixtureURL);
    await expect(rail(page)).toContainText("4", { timeout: 45_000 });
    await rail(page).click();
    await expect(row(page)).toBeVisible();
    await expect(page.locator("[data-reader-status]")).toHaveText("success");
}

test("authoritative exact/null states survive native snapshot conversion, four locales and unread-card cardinality", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await openChanges(page);
        await fixture.settle();
        expect(fixture.reads).toHaveLength(1);
        expect(fixture.summaryReads).toHaveLength(1);
        const titleBox = await row(page).getByText("Exact card", { exact: true }).boundingBox();
        expect(titleBox?.width).toBeGreaterThan(150);
        expect(titleBox?.height).toBeLessThan(30);
        await page.screenshot({ path: testInfo.outputPath("changes-en.png") });
        await expect(row(page).locator("[data-unread-change-count]")).toHaveText("3 unread changes");
        await expect(row(page, "Historical card").locator("[data-unread-change-count]")).toHaveText("Change count unavailable");
        await expect(row(page, "Legacy card").locator("[data-unread-change-count]")).toHaveText("Change count unavailable");
        await expect(row(page, "Manual card").locator("[data-unread-change-count]")).toHaveText("Marked unread");
        await expect(row(page, "Read card")).toHaveCount(0);
        await expect(row(page, "Mirror card")).toHaveCount(0);
        expect(await modelReceipt(page)).toMatchObject({ count: 3, status: "exact", sequence: 100, target: "latest-comment" });
        for (const [language, exact, manual, unknown] of [
            ["ko-KR", "읽지 않은 변경 3개", "읽지 않음으로 표시됨", "변경 수 확인 불가"],
            ["ja-JP", "未読の変更 3件", "未読に設定済み", "変更数を確認できません"],
            ["zh-CN", "3 项未读更改", "已标记为未读", "更改数量不可用"],
            ["en-US", "3 unread changes", "Marked unread", "Change count unavailable"],
        ]) {
            await page.evaluate(async (lng) => {
                const path = "/src/i18n.ts";
                const { default: i18n } = await import(/* @vite-ignore */ path);
                await i18n.changeLanguage(lng);
            }, language);
            await expect(page.locator("[data-unread-change-count]", { hasText: exact })).toHaveCount(1);
            await expect(page.locator("[data-unread-change-count]", { hasText: manual })).toHaveCount(1);
            await expect(page.locator("[data-unread-change-count]", { hasText: unknown })).toHaveCount(2);
        }
        fixture.cards[0].unread_change_count = 1;
        await sidebar(page).getByRole("button", { name: "Refresh", exact: true }).click();
        await expect(row(page).locator("[data-unread-change-count]")).toHaveText("1 unread change");
        await expect(rail(page)).toContainText("4");
        await row(page).click();
        await expect(page.locator("[data-native-location]")).toHaveText("/board/left/left-card");
        await expect(page.locator("[data-unread-target]")).toHaveText("comments:latest-comment");
        await expect.poll(() => fixture.pendingSeen.length).toBe(1);
        await fixture.replySeen({ card_uid: "left-card", seen_change_seq: 100 });
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.save();
    }
});

test("native change-count socket refresh ignores broadcast counts and other board topics", async ({ page }, testInfo) => {
    const fixture = await nativeFixture(page, testInfo);
    try {
        await openChanges(page);
        await fixture.settle();
        fixture.emit({ change_count_changed: true }, "other-board");
        await fixture.settle();
        expect(fixture.reads).toHaveLength(1);
        fixture.cards[0].unread_change_count = 5;
        fixture.cards[0].last_change_seq = 103;
        fixture.hold();
        fixture.emit({ change_count_changed: true, unread_change_count: 999, unread_change_count_status: "exact" });
        await expect.poll(() => fixture.held.length).toBe(1);
        await expect(row(page).locator("[data-unread-change-count]")).toHaveText("3 unread changes");
        await fixture.release();
        await expect(row(page).locator("[data-unread-change-count]")).toHaveText("5 unread changes");
        await fixture.settle();
        fixture.emit({ change_count_changed: true });
        await fixture.settle();
        await expect(row(page).locator("[data-unread-change-count]")).toHaveText("5 unread changes");
        await expect(rail(page)).toContainText("4");
        fixture.emit({ read_state_changed: true });
        await expect.poll(async () => (await modelReceipt(page))?.readVersion).toBe(1);
        expect(fixture.reads).toHaveLength(3);
        expect(fixture.errors).toEqual([]);
    } finally {
        await fixture.release();
        await fixture.save();
    }
});

for (const receipt of ["current", "newer", "pending-newer", "missing", "wrong-card", "out-of-order"]) {
    test(`native seen receipt preserves authoritative counts: ${receipt}`, async ({ page }, testInfo) => {
        const fixture = await nativeFixture(page, testInfo);
        try {
            await openChanges(page);
            await fixture.settle();
            await page.getByRole("button", { name: "Record seen receipt", exact: true }).click();
            await expect.poll(() => fixture.pendingSeen.length).toBe(1);
            if (receipt === "out-of-order") {
                await page.getByRole("button", { name: "Record seen receipt", exact: true }).click();
                await expect.poll(() => fixture.pendingSeen.length).toBe(2);
                fixture.cards[0].unread_change_count = 0;
                fixture.cards[0].has_unread_change = false;
                await fixture.replySeen({ card_uid: "left-card", seen_change_seq: 100 }, 1);
                await expect(row(page)).toHaveCount(0);
                await fixture.settle();
            }
            if (["newer", "pending-newer", "out-of-order"].includes(receipt)) {
                fixture.cards[0].unread_change_count = 2;
                fixture.cards[0].has_unread_change = true;
                fixture.cards[0].last_change_seq = 102;
                if (receipt === "pending-newer") fixture.hold();
                fixture.emit({ change_count_changed: true });
                if (receipt === "pending-newer") {
                    await expect.poll(() => fixture.held.length).toBe(1);
                    expect((await modelReceipt(page))?.sequence).toBe(100);
                } else {
                    await expect(row(page).locator("[data-unread-change-count]")).toHaveText("2 unread changes");
                    await fixture.settle();
                }
            } else if (receipt === "current") {
                fixture.cards[0].unread_change_count = 0;
                fixture.cards[0].has_unread_change = false;
            }
            fixture.hold();
            await fixture.replySeen(
                receipt === "missing" ? null : { card_uid: receipt === "wrong-card" ? "another-card" : "left-card", seen_change_seq: 100 }
            );
            await expect.poll(() => fixture.held.length).toBe(receipt === "pending-newer" ? 2 : 1);
            await expect(row(page)).toBeVisible();
            expect((await modelReceipt(page))?.unread).toBe(true);
            await fixture.release();
            await fixture.settle();
            await expect(page.locator("[data-seen-status]")).toHaveText("success");
            if (receipt === "current") {
                await expect(row(page)).toHaveCount(0);
                expect(await modelReceipt(page)).toMatchObject({ unread: false, count: 0, status: "exact" });
                await expect(rail(page)).toContainText("3");
                const count = fixture.reads.length;
                await page.getByRole("button", { name: "Record seen receipt", exact: true }).click();
                await expect.poll(() => fixture.pendingSeen.length).toBe(1);
                await fixture.replySeen({ card_uid: "left-card", seen_change_seq: 100 });
                await expect(page.locator("[data-seen-status]")).toHaveText("success");
                await fixture.settle();
                expect(fixture.reads).toHaveLength(count);
            } else {
                const count = ["newer", "pending-newer", "out-of-order"].includes(receipt) ? 2 : 3;
                await expect(row(page).locator("[data-unread-change-count]")).toHaveText(`${count} unread changes`);
            }
            expect(fixture.readerReads()).toBeGreaterThan(1);
            expect(fixture.errors).toEqual([]);
        } finally {
            await fixture.release();
            await fixture.save();
        }
    });
}

for (const transition of ["actor", "session"]) {
    test(`late native seen receipt cannot change a replacement ${transition}`, async ({ page }, testInfo) => {
        const fixture = await nativeFixture(page, testInfo);
        try {
            await openChanges(page);
            await fixture.settle();
            await page.getByRole("button", { name: "Record seen receipt", exact: true }).click();
            await expect.poll(() => fixture.pendingSeen.length).toBe(1);
            if (transition === "actor") fixture.setActor("replacement-user");
            fixture.cards[0].unread_change_count = 7;
            await updateSession(page, "replacement-session");
            await expect.poll(async () => (await modelReceipt(page))?.count).toBe(7);
            await fixture.settle();
            if (!(await sidebar(page).isVisible())) await rail(page).click();
            await expect(row(page).locator("[data-unread-change-count]")).toHaveText("7 unread changes");
            await fixture.settle();
            const reads = fixture.reads.length;
            await fixture.replySeen({ card_uid: "left-card", seen_change_seq: 100 });
            await expect(page.locator("[data-seen-status]")).toHaveText("error");
            await fixture.settle();
            expect(fixture.reads).toHaveLength(reads);
            expect(await modelReceipt(page)).toMatchObject({ unread: true, count: 7, status: "exact" });
            fixture.cards[0].unread_change_count = 8;
            fixture.emit({ change_count_changed: true });
            await expect(row(page).locator("[data-unread-change-count]")).toHaveText("8 unread changes");
            expect(fixture.reads.at(-1)?.token).toBe("Bearer replacement-session");
            expect(fixture.errors).toEqual([]);
        } finally {
            await fixture.save();
        }
    });
}
