import { test, expect, type Page } from "@playwright/test";
const verificationDir = process.env.LANGBOARD_SCHEDULE_VERIFICATION_DIR || "test-results/schedule-workload";
async function scheduleFixture(page: Page) {
    page.on("pageerror", (error) => console.log("Fixture page error:", error.stack));
    await page.routeWebSocket(/.*/, () => {});
    const now = new Date().toISOString();
    let actor = "fixture-user";
    const reads: URL[] = [];
    const exports: URL[] = [];
    const workloadReads: URL[] = [];
    let incomplete = false;
    const exportTokens: (string | undefined)[] = [];
    let fail = false;
    let exportFail = false;
    let hold = false;
    let release: (() => void) | undefined;
    const releases: (() => void)[] = [];
    const user = () => ({
        uid: actor,
        created_at: now,
        updated_at: now,
        firstname: "Fixture",
        lastname: "User",
        user_groups: [],
        type: "user",
        preferred_lang: "en",
        api_key_role_actions: ["*"],
        setting_role_actions: ["*"],
        mcp_role_actions: ["*"],
        subemails: [],
        notification_unsubs: {},
    });
    const project = (uid = "left") => ({
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
    });
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const url = new URL(route.request().url());
            const path = url.pathname;
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            if (path === "/dashboard/schedule/ical") {
                exports.push(url);
                exportTokens.push(route.request().headers().authorization);
                if (hold)
                    await new Promise<void>((resolve) => {
                        release = resolve;
                        releases.push(resolve);
                    });
                return route.fulfill({
                    status: exportFail ? 409 : 200,
                    contentType: "text/calendar",
                    body: exportFail ? "export cap" : "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
                });
            }
            if (path === "/dashboard/schedule/workload") {
                workloadReads.push(url);
                const personal = url.searchParams.get("scope") === "personal";
                const captured = actor;
                if (hold)
                    await new Promise<void>((resolve) => {
                        release = resolve;
                        releases.push(resolve);
                    });
                if (fail) return route.fulfill({ status: 500, json: { detail: "fixture failure" } });
                return route.fulfill({
                    json: {
                        scope: url.searchParams.get("scope"),
                        project_uid: personal ? null : url.searchParams.get("project_uid"),
                        start: url.searchParams.get("start"),
                        end: url.searchParams.get("end"),
                        as_of: "2026-03-08T16:00:00+00:00",
                        time_attribution: "all_work_on_selected_cards",
                        time_includes_currently_completed_and_archived_cards: true,
                        workload: { open_count: captured === "second-user" ? 77 : personal ? 3 : 12, incomplete_count: personal ? 2 : 5 },
                        time: {
                            item_count: 9,
                            known_invested_seconds: 120,
                            unknown_item_count: incomplete ? 2 : 0,
                            invested_seconds: incomplete ? null : 120,
                            running_item_count: incomplete ? null : 1,
                            evidence: incomplete ? "incomplete" : "complete",
                        },
                    },
                });
            }
            if (path === "/dashboard/schedule") {
                reads.push(url);
                const oldActor = actor;
                if (hold)
                    await new Promise<void>((resolve) => {
                        release = resolve;
                        releases.push(resolve);
                    });
                if (fail) return route.fulfill({ status: 400, json: { detail: "invalid range" } });
                const personal = url.searchParams.get("scope") === "personal";
                const page2 = url.searchParams.has("cursor");
                const items = personal
                    ? []
                    : (page2 ? [1, 2] : [0, 1]).map((index) => ({
                          card_uid: `card${index}`,
                          title: `${oldActor} deadline ${index}`,
                          project_uid: url.searchParams.get("project_uid") ?? "left",
                          project_title: `${url.searchParams.get("project_uid") ?? "left"} board`,
                          column_uid: "column",
                          column_name: "Doing",
                          deadline_at: now,
                          work_state: { workflow_stage: "review", material_kind: "work" },
                      }));
                return route.fulfill({
                    json: {
                        items,
                        next_cursor: !personal && !page2 ? "same-deadline-keyset" : null,
                        start: url.searchParams.get("start"),
                        end: url.searchParams.get("end"),
                        scope: url.searchParams.get("scope"),
                    },
                });
            }
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user: user(), bots: [] };
            else if (["/board/left", "/board/right"].includes(path)) json = { project: project(path.split("/")[2]) };
            else if (["/board/left/cards", "/board/right/cards"].includes(path))
                json = { cards: [], columns: [], checklists: [], global_relationships: [] };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project")) json = { projects: [project(), project("right")], columns: [], records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            await route.fulfill({ json });
        }
    );
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("left board", { timeout: 45_000 });
    return {
        reads,
        workloadReads,
        setIncomplete: (value: boolean) => {
            incomplete = value;
        },
        exports,
        exportTokens,
        setFail: (value: boolean) => {
            fail = value;
        },
        setExportFail: (value: boolean) => {
            exportFail = value;
        },
        setActor: (value: string) => {
            actor = value;
        },
        hold: () => {
            hold = true;
            release = undefined;
        },
        release: () => {
            hold = false;
            for (const resolve of releases.splice(0)) resolve();
        },
        held: () => !!release,
    };
}

async function openSchedule(page: Page, mobile = false) {
    if (mobile) {
        await page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "More", exact: true }).click();
        await page.getByRole("menuitem", { name: "Schedule", exact: true }).click();
    } else await page.getByRole("button", { name: "Schedule", exact: true }).click();
    return page.getByRole("navigation", { name: "Schedule", exact: true });
}

test("Today uses local DST day; deadlines keep month, pages and iCal", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-03-08T16:00:00Z"));
    const fixture = await scheduleFixture(page);
    const panel = await openSchedule(page);
    await expect(panel.locator("li")).toHaveCount(2);
    const read = fixture.reads.at(-1)!;
    expect(read.searchParams.get("start")).toBe("2026-03-08T05:00:00.000Z");
    expect(read.searchParams.get("end")).toBe("2026-03-09T04:00:00.000Z");
    await expect(panel.getByLabel("Start date", { exact: true })).toHaveCount(0);
    await panel.getByRole("button", { name: "Deadlines", exact: true }).click();
    await expect(panel.getByLabel("Start date", { exact: true })).toHaveValue("2026-03-01");
    await panel.getByRole("button", { name: "Load more", exact: true }).click();
    await expect(panel.locator("li")).toHaveCount(3);
    const downloaded = page.waitForEvent("download");
    await panel.getByRole("button", { name: "Download iCal", exact: true }).click();
    expect((await downloaded).suggestedFilename()).toBe("schedule-board.ics");
    await panel.getByRole("button", { name: "Today", exact: true }).click();
    await expect.poll(() => fixture.reads.at(-1)?.searchParams.get("end")).toBe("2026-03-09T04:00:00.000Z");
});

test("Workload separates snapshot, range and attribution; incomplete evidence and retry", async ({ page }) => {
    const fixture = await scheduleFixture(page);
    const panel = await openSchedule(page);
    await panel.getByRole("button", { name: "Workload", exact: true }).click();
    const section = panel.getByRole("region", { name: "Workload", exact: true });
    await expect(section.getByText("12", { exact: true })).toBeVisible();
    await expect(section.getByText("5", { exact: true })).toBeVisible();
    await expect(section).toContainText("independent of the selected dates");
    await expect(section).toContainText("All workers");
    await expect(section).toContainText("Includes currently completed and archived cards");
    await expect(section).toContainText("Server snapshot");
    fixture.setIncomplete(true);
    await section.getByRole("button", { name: "Refresh workload" }).click();
    await expect(section.getByText("Unavailable", { exact: true })).toHaveCount(2);
    await expect(section).toContainText("Known partial time: 120 seconds (not the total)");
    await expect(section).toContainText("Items with unknown time: 2");
    fixture.setFail(true);
    await section.getByRole("button", { name: "Refresh workload" }).click();
    await expect(section.getByRole("alert")).toContainText("Could not load workload");
    await expect(section.getByText("12", { exact: true })).toHaveCount(0);
    await expect(section.getByText("Known partial time: 120 seconds (not the total)", { exact: true })).toHaveCount(0);
    fixture.setFail(false);
    fixture.setIncomplete(false);
    await section.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(section.getByText("Unavailable", { exact: true })).toHaveCount(0);
    await panel.getByRole("button", { name: "Personal schedule", exact: true }).click();
    await expect(section.getByText("3", { exact: true })).toBeVisible();
    expect(fixture.workloadReads.at(-1)!.searchParams.has("project_uid")).toBe(false);
    await page.screenshot({ path: `${verificationDir}/workload-desktop.png` });
});

test("Workload drops delayed scope, session and actor results and honors permission revocation", async ({ page }) => {
    const fixture = await scheduleFixture(page);
    const panel = await openSchedule(page);
    await panel.getByRole("button", { name: "Workload", exact: true }).click();
    const section = panel.getByRole("region", { name: "Workload", exact: true });
    await expect(section.getByText("12", { exact: true })).toBeVisible();
    fixture.hold();
    await section.getByRole("button", { name: "Refresh workload" }).click();
    await expect.poll(fixture.held).toBe(true);
    await panel.getByRole("button", { name: "Personal schedule", exact: true }).click();
    await expect(section.getByText("12", { exact: true })).toHaveCount(0);
    fixture.release();
    await expect(section.getByText("3", { exact: true })).toBeVisible();
    fixture.hold();
    await section.getByRole("button", { name: "Refresh workload" }).click();
    await expect.poll(fixture.held).toBe(true);
    const beforeRotation = fixture.workloadReads.length;
    await page.evaluate(async () => {
        const p = "/src/core/stores/AuthStore.ts",
            a = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ p), import(/* @vite-ignore */ a)]);
        await store.getState().updateToken("workload-rotated", api);
    });
    fixture.release();
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("left board");
    if (!(await panel.isVisible())) await openSchedule(page);
    await panel.getByRole("button", { name: "Workload", exact: true }).click();
    await panel.getByRole("button", { name: "Personal schedule", exact: true }).click();
    await expect.poll(() => fixture.workloadReads.length).toBeGreaterThan(beforeRotation);
    await expect(section.getByText("3", { exact: true })).toBeVisible();
    fixture.hold();
    await section.getByRole("button", { name: "Refresh workload" }).click();
    await expect.poll(fixture.held).toBe(true);
    fixture.setActor("second-user");
    await page.evaluate(async () => {
        const p = "/src/core/stores/AuthStore.ts",
            a = "/src/core/helpers/Api.ts";
        const [{ default: store }, { api }] = await Promise.all([import(/* @vite-ignore */ p), import(/* @vite-ignore */ a)]);
        await store.getState().updateToken("workload-second-user", api);
    });
    fixture.release();
    await expect(page.getByRole("contentinfo", { name: "Workbench status" })).toContainText("left board");
    if (!(await panel.isVisible())) await openSchedule(page);
    await panel.getByRole("button", { name: "Workload", exact: true }).click();
    await expect(section.getByText("77", { exact: true })).toBeVisible();
    await expect(section.getByText("3", { exact: true })).toHaveCount(0);
    await page.evaluate(async () => {
        const p = "/src/core/models/index.ts";
        const { Project } = await import(/* @vite-ignore */ p);
        Project.Model.getModel("left").current_auth_role_actions = [];
    });
    await expect(panel.getByRole("button", { name: "Board schedule", exact: true })).toBeDisabled();
});

test("Narrow drawer supports keyboard views, valid ranges and overflow bounds", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const fixture = await scheduleFixture(page);
    const panel = await openSchedule(page, true);
    const workload = panel.getByRole("button", { name: "Workload", exact: true });
    await workload.focus();
    await page.keyboard.press("Enter");
    await expect(workload).toHaveAttribute("aria-pressed", "true");
    await expect(panel.getByRole("region", { name: "Workload" }).getByText("12", { exact: true })).toBeVisible();
    const before = fixture.workloadReads.length;
    await panel.getByLabel("End date (exclusive)", { exact: true }).fill("2028-01-01");
    await expect(panel.getByRole("alert")).toContainText("within 93 days");
    expect(fixture.workloadReads.length).toBe(before);
    await panel.getByRole("button", { name: "Today", exact: true }).focus();
    await page.keyboard.press("Space");
    await expect(panel.getByRole("button", { name: "Today", exact: true })).toHaveAttribute("aria-pressed", "true");
    const bounds = await panel.evaluate((el) => ({ width: el.clientWidth, scroll: el.scrollWidth }));
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
    await page.screenshot({ path: `${verificationDir}/schedule-mobile.png` });
    await page.keyboard.press("Escape");
    await expect(panel).not.toBeVisible();
});

test("All supported locales keep workload controls and evidence readable at narrow width", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await scheduleFixture(page);
    const panel = await openSchedule(page, true);
    await panel.getByRole("button", { name: "Workload", exact: true }).click();
    for (const [locale, label] of [
        ["en-US", "Workload"],
        ["ko-KR", "업무량"],
        ["ja-JP", "作業量"],
        ["zh-CN", "工作量"],
    ]) {
        await page.evaluate(async (language) => {
            const path = "/src/i18n.ts";
            const { default: i18n } = await import(/* @vite-ignore */ path);
            await i18n.changeLanguage(language);
        }, locale);
        const localizedPanel = page.locator("nav[data-workbench-context]").filter({ has: page.getByRole("region", { name: label, exact: true }) });
        await expect(localizedPanel.getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
        await expect(localizedPanel.getByRole("region", { name: label, exact: true }).getByText("12", { exact: true })).toBeVisible();
        const bounds = await localizedPanel.evaluate((el) => ({ width: el.clientWidth, scroll: el.scrollWidth }));
        expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
    }
});
