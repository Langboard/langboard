import { test, expect, type Request } from "@playwright/test";
import { gunzipSync } from "node:zlib";
import { DomainEventType } from "@/core/events/DomainEventType";
import type { IWorkflowRule, IWorkflowStage } from "@/controllers/api/settings/workflowStages/useWorkflowStages";

const requestJSON = (request: Request) => {
    const body = request.postDataBuffer();
    if (!body) return {};
    return JSON.parse((request.headers()["content-encoding"] === "gzip" ? gunzipSync(body) : body).toString());
};

const existing = (): IWorkflowStage => ({
    revision: "a".repeat(64),
    uid: "existing",
    key: "closed",
    name: "Closed",
    description: "Accepted work",
    color: "#64748B",
    order: 0,
    is_builtin: true,
    is_active: true,
    counts_as_completed: true,
    active_queue_policy: "exclude",
    overdue_policy: "suppress",
    confirm_descendant_move: false,
    rules: [],
    translations: {
        en: { name: "Closed", description: "Accepted work" },
        ko: { name: "완료", description: "완료 업무" },
        ja: { name: "完了", description: "完了業務" },
        zh: { name: "已完成", description: "已完成工作" },
    },
});

const botRule = (botUID: string): IWorkflowRule => ({
    uid: "saved-rule",
    revision: 7,
    name: "Saved bot rule",
    is_enabled: true,
    trigger: { kind: "event", event_type: DomainEventType.CardCommentAdded },
    condition_match: "all",
    conditions: [],
    actions: [
        {
            uid: "saved-action",
            kind: "bot.invoke",
            executor: { kind: "bot", uid: botUID },
            config: { bot_uid: botUID },
            requires_previous_success: false,
        },
    ],
});

const effectRun = (uid: string, started = "2026-10-21T03:00:00Z") => ({
    uid,
    stage_revision: "b".repeat(64),
    source: "entry",
    status: "succeeded",
    effects: ["complete_checkitems", "stop_running_timers", "notify_author_on_entry"],
    started_at: started,
    finished_at: started,
    duration_ms: 1234,
    result: { completed: 3, stopped: 2, notified: 1 },
    project_uid: "board-source",
    card_uid: "card-source",
    actor_uid: "qa",
    actor_type: "user",
});

for (const width of [1440, 390]) {
    for (const pendingRead of ["initial", "pagination"]) {
        test(`workflow history ${width}px discards ${pendingRead} reads across permission revocation`, async ({ page }) => {
            await page.setViewportSize({ width, height: 1000 });
            let writes = 0;
            let historyReads = 0;
            let oldFinished = false;
            let releaseOld!: () => void;
            let releaseCurrent!: () => void;
            const oldPending = new Promise<void>((resolve) => {
                releaseOld = resolve;
            });
            const currentPending = new Promise<void>((resolve) => {
                releaseCurrent = resolve;
            });
            const pendingIndex = pendingRead === "pagination" ? 2 : 1;
            await page.route("**/settings/workflow-stages**", async (route) => {
                if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
                if (route.request().method() !== "GET") {
                    writes++;
                    return route.fulfill({ status: 500 });
                }
                const url = new URL(route.request().url());
                if (!url.pathname.endsWith("/history")) return route.fulfill({ json: { stages: [existing()] } });
                const read = ++historyReads;
                if (read === pendingIndex) await oldPending;
                if (read > pendingIndex) await currentPending;
                await route.fulfill({
                    json: {
                        items: [],
                        legacy_items: [effectRun(`run-${read}`, read > pendingIndex ? "2026-10-27T00:00:00Z" : "2020-01-01T00:00:00Z")],
                        next_cursor: read < pendingIndex ? "older" : null,
                    },
                });
                if (read === pendingIndex) oldFinished = true;
            });
            await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
            await page.getByRole("button", { name: /^Closed/ }).click();
            await page.getByRole("tab", { name: "History", exact: true }).click();
            const history = page.getByRole("region", { name: "Effect history", exact: true });
            if (pendingRead === "pagination") {
                await expect(history.locator("time")).toHaveCount(1);
                await history.getByRole("button", { name: "More history", exact: true }).click();
            }
            await expect.poll(() => historyReads).toBe(pendingIndex);
            await page.evaluate(() => window.dispatchEvent(new CustomEvent("workflow-fixture-roles", { detail: [] })));
            await expect(page.getByRole("alert")).toContainText("History access unavailable");
            await expect(page.locator("time")).toHaveCount(0);
            releaseOld();
            await expect.poll(() => oldFinished).toBe(true);
            await page.evaluate(() => window.dispatchEvent(new CustomEvent("workflow-fixture-roles", { detail: ["*"] })));
            await expect.poll(() => historyReads).toBe(pendingIndex + 1);
            await expect(history.locator("time")).toHaveCount(0);
            await expect(history.getByRole("status")).toContainText("Loading");
            releaseCurrent();
            await expect(history.locator("time")).toHaveCount(1);
            await expect(history.locator("time")).toHaveAttribute("datetime", "2026-10-27T00:00:00Z");
            expect(writes).toBe(0);
        });
    }

    for (const authChange of ["session", "identity", "logout"]) {
        test(`workflow history ${width}px rejects outstanding reads after native ${authChange} change`, async ({ page }) => {
            await page.setViewportSize({ width, height: 1000 });
            let historyReads = 0;
            let oldFinished = false;
            let releaseOld!: () => void;
            let releaseCurrent!: () => void;
            const oldPending = new Promise<void>((resolve) => {
                releaseOld = resolve;
            });
            const currentPending = new Promise<void>((resolve) => {
                releaseCurrent = resolve;
            });
            await page.route("**/auth/me", (route) =>
                route.fulfill({
                    json: {
                        user: {
                            uid: authChange === "identity" ? "other-actor" : "qa",
                            created_at: "2026-10-27T00:00:00Z",
                            updated_at: "2026-10-27T00:00:00Z",
                            type: "user",
                            email: "qa@example.test",
                            firstname: "QA",
                            lastname: "",
                            username: "qa",
                            is_admin: true,
                            user_groups: [],
                            subemails: [],
                            setting_role_actions: ["*"],
                            api_key_role_actions: [],
                            mcp_role_actions: [],
                        },
                        bots: [],
                    },
                })
            );
            await page.route("**/settings/workflow-stages**", async (route) => {
                if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
                const url = new URL(route.request().url());
                if (!url.pathname.endsWith("/history")) return route.fulfill({ json: { stages: [existing()] } });
                const read = ++historyReads;
                await (read === 1 ? oldPending : currentPending);
                await route.fulfill({
                    json: {
                        items: [],
                        legacy_items: [effectRun(`run-${read}`, read === 1 ? "2020-01-01T00:00:00Z" : "2026-10-27T00:00:00Z")],
                        next_cursor: null,
                    },
                });
                if (read === 1) oldFinished = true;
            });
            await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
            await page.getByRole("button", { name: /^Closed/ }).click();
            await page.getByRole("tab", { name: "History", exact: true }).click();
            await expect.poll(() => historyReads).toBe(1);
            await page.evaluate((change) => {
                window.dispatchEvent(new Event(change === "logout" ? "workflow-fixture-logout" : "workflow-fixture-authenticate"));
            }, authChange);
            if (authChange === "session") {
                await expect.poll(() => historyReads).toBe(2);
            } else {
                await expect(page.getByRole("alert")).toContainText("History access unavailable");
            }
            releaseOld();
            await expect.poll(() => oldFinished).toBe(true);
            await expect(page.locator("time")).toHaveCount(0);
            if (authChange === "session") {
                releaseCurrent();
                await expect(page.locator("time")).toHaveAttribute("datetime", "2026-10-27T00:00:00Z");
            } else if (authChange === "logout") {
                expect(historyReads).toBe(1);
            } else {
                releaseCurrent();
                await expect(page.getByRole("alert")).toContainText("History access unavailable");
                await expect(page.locator("time")).toHaveCount(0);
            }
        });
    }

    test(`workflow effect history ${width}px pages without writes, preserves draft and hides revoked results`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        let writes = 0;
        let denied = false;
        const cursors: (string | null)[] = [];
        await page.route("**/settings/workflow-stages**", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            if (route.request().method() !== "GET") {
                writes++;
                return route.fulfill({ status: 500 });
            }
            const url = new URL(route.request().url());
            if (!url.pathname.endsWith("/history")) return route.fulfill({ json: { stages: [existing()] } });
            if (denied) return route.fulfill({ status: 403, json: {} });
            cursors.push(url.searchParams.get("before"));
            return route.fulfill({
                json: url.searchParams.has("before")
                    ? { items: [], legacy_items: [effectRun("one"), effectRun("two")], next_cursor: null }
                    : { items: [], legacy_items: [effectRun("one")], next_cursor: "one" },
            });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByLabel("Stage name", { exact: true }).fill("Unsaved stage");
        await page.getByRole("tab", { name: "History", exact: true }).click();
        const history = page.getByRole("region", { name: "Effect history", exact: true });
        await expect(history.getByText("Succeeded", { exact: true })).toHaveCount(1);
        await history.getByText("Effect details", { exact: true }).click();
        await expect(history.getByText(`Revision: ${"b".repeat(64)}`, { exact: true })).toBeVisible();
        await expect(history.getByText(`Revision: ${existing().revision}`, { exact: true })).toHaveCount(0);
        await expect(history.getByText("Completed items: 3", { exact: true })).toBeVisible();
        await expect(history.getByText("Notifications created: 1", { exact: true })).toBeVisible();
        await expect(history.getByRole("link", { name: "Open card" })).toHaveAttribute("href", "/board/board-source/card-source");
        await expect(history.getByRole("link", { name: "Open card" })).toHaveAttribute("target", "_blank");
        await history.getByRole("button", { name: "More history" }).click();
        await expect(history.getByText("Succeeded", { exact: true })).toHaveCount(2);
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-history-${width}-light.png` });
        await page.evaluate(() => document.documentElement.classList.add("dark"));
        const darkBackground = await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor);
        expect(darkBackground).not.toBe("rgb(255, 255, 255)");
        await expect(page.getByRole("dialog")).toHaveCSS("background-color", darkBackground);
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-history-${width}-dark.png` });
        expect(cursors).toContain("one");
        expect(writes).toBe(0);
        denied = true;
        await history.getByRole("button", { name: "Refresh" }).click();
        await expect(history.getByRole("alert")).toContainText("Could not load effect history");
        await expect(history.getByText("Succeeded", { exact: true })).toHaveCount(0);
        await page.getByRole("tab", { name: "Settings", exact: true }).click();
        await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Unsaved stage");
        expect(writes).toBe(0);
    });

    test(`workflow history ${width}px ignores late stage response and clears after logout`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        let release!: () => void;
        let oldRequested = false;
        let oldFinished = false;
        const pending = new Promise<void>((resolve) => {
            release = resolve;
        });
        const review = {
            ...existing(),
            uid: "review-stage",
            key: "review",
            name: "Review",
            translations: { ...existing().translations, en: { name: "Review", description: "Review work" } },
        };
        await page.route("**/settings/workflow-stages**", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            const url = new URL(route.request().url());
            if (!url.pathname.endsWith("/history")) return route.fulfill({ json: { stages: [existing(), review] } });
            if (url.pathname.includes("/existing/")) {
                oldRequested = true;
                await pending;
            }
            await route.fulfill({
                json: {
                    items: [],
                    legacy_items: [effectRun("run", url.pathname.includes("/existing/") ? "2020-01-01T00:00:00Z" : "2026-10-21T03:00:00Z")],
                    next_cursor: null,
                },
            });
            if (url.pathname.includes("/existing/")) oldFinished = true;
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("tab", { name: "History", exact: true }).click();
        await expect.poll(() => oldRequested).toBe(true);
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: /^Review/ }).click();
        await page.getByRole("tab", { name: "History", exact: true }).click();
        await expect(page.locator("time")).toHaveAttribute("datetime", "2026-10-21T03:00:00Z");
        release();
        await expect.poll(() => oldFinished).toBe(true);
        await expect(page.locator("time")).toHaveAttribute("datetime", "2026-10-21T03:00:00Z");
        await page.evaluate(() => window.dispatchEvent(new Event("workflow-fixture-logout")));
        await expect(page.getByRole("alert")).toContainText("History access unavailable");
        await expect(page.locator("time")).toHaveCount(0);
    });

    test(`workflow history ${width}px keyboard navigation, empty state and unsaved stage guard`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        await page.route("**/settings/workflow-stages**", (route) =>
            route.fulfill({
                json: new URL(route.request().url()).pathname.endsWith("/history")
                    ? { items: [], legacy_items: [], next_cursor: null }
                    : { stages: [existing()] },
            })
        );
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("tab", { name: "Settings", exact: true }).focus();
        await page.keyboard.press("ArrowRight");
        await expect(page.getByRole("tab", { name: "History", exact: true })).toHaveAttribute("aria-selected", "true");
        await expect(page.getByText("No recorded effects", { exact: true })).toBeVisible();
        await page.keyboard.press("Home");
        await expect(page.getByLabel("Machine key", { exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "New stage", exact: true }).click();
        await expect(page.getByRole("tab", { name: "History", exact: true })).toBeDisabled();
    });
}

test("workflow stage submission owns one write before pending render and releases after conflict", async ({ page }) => {
    let writes = 0;
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/workflow-stages**", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
        if (route.request().method() === "GET") return route.fulfill({ json: { stages: [existing()] } });
        writes++;
        await pending;
        return route.fulfill({ status: 409, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
    await page.getByRole("button", { name: /^Closed/ }).click();
    await page.getByLabel("Stage name", { exact: true }).fill("Accepted");
    await page.locator("form").evaluate((form) => {
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    try {
        await expect(page.locator("form button[type=submit]")).toBeDisabled();
        await expect.poll(() => writes).toBeGreaterThan(0);
        await page.waitForTimeout(100);
        expect(writes).toBe(1);
    } finally {
        release();
    }
    await expect(page.getByRole("alert")).toContainText("This stage changed elsewhere");
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Accepted");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => writes).toBe(2);
});

test("deactivation excludes a simultaneous stage save", async ({ page }) => {
    const writes: string[] = [];
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/workflow-stages**", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
        if (route.request().method() === "GET") return route.fulfill({ json: { stages: [existing()] } });
        writes.push(route.request().url());
        await pending;
        return route.fulfill({ status: 409, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
    await page.getByRole("button", { name: /^Closed/ }).click();
    await page.getByRole("button", { name: "Deactivate", exact: true }).click();
    await page
        .getByRole("dialog", { name: "Deactivate", exact: true })
        .getByRole("button", { name: "Deactivate", exact: true })
        .evaluate((button) => {
            (button as HTMLButtonElement).click();
            document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        });
    try {
        await expect.poll(() => writes.length).toBeGreaterThan(0);
        await page.waitForTimeout(100);
        expect(writes).toHaveLength(1);
        expect(writes[0]).toMatch(/\/deactivate$/);
    } finally {
        release();
    }
    await expect(page.getByRole("alert")).toContainText("This stage changed elsewhere");
    await expect(page.getByRole("button", { name: "Deactivate", exact: true })).toBeEnabled();
});

for (const width of [1280, 390])
    test(`registry edit roundtrip and failure preserve draft at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        let stages: IWorkflowStage[] = [existing()];
        let fail = true;
        let saves = 0;
        await page.route("**/settings/workflow-stages**", async (route) => {
            const headers = {
                "Access-Control-Allow-Origin": new URL(test.info().project.use.baseURL!).origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
                "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
            };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            if (route.request().method() === "GET") return route.fulfill({ headers, json: { stages } });
            if (route.request().url().endsWith("/deactivate")) {
                stages = stages.map((stage) => ({ ...stage, is_active: false }));
                return route.fulfill({ headers, json: { stage: stages[0] } });
            }
            saves++;
            if (fail) {
                fail = false;
                return route.fulfill({ status: 500, headers, json: { error: "test unavailable" } });
            }
            const stage = { ...requestJSON(route.request()), revision: "b".repeat(64), uid: "saved", is_active: true, is_builtin: false };
            stages = [stage];
            return route.fulfill({ headers, json: { stage } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Machine key", { exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await page.getByRole("button", { name: "New stage", exact: true }).click();
        await page.getByLabel("Machine key", { exact: true }).fill("released");
        await page.getByLabel("Stage name", { exact: true }).fill("Released");
        await page.getByLabel("Stage description", { exact: true }).fill("Accepted delivery");
        await page.getByRole("button", { name: "Color", exact: true }).click();
        const picker = page.locator("[data-radix-popper-content-wrapper]").last();
        await expect(picker.locator(".react-colorful")).toBeVisible();
        await picker.locator("input").fill("#FF9500");
        await expect(picker.locator("input")).toHaveValue("#FF9500");
        await picker.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByLabel("Color hex code", { exact: true })).toHaveValue("#FF9500");
        expect(saves).toBe(0);
        await page.getByLabel("Color hex code", { exact: true }).fill("#8B5CF6");
        await page.getByLabel("Count as completed", { exact: true }).check();
        await page.getByLabel("Active queue", { exact: true }).selectOption("exclude");
        await page.getByLabel("Hide overdue", { exact: true }).check();
        await page.getByRole("button", { name: "Add rule", exact: true }).click();
        await page.getByLabel("Rule name", { exact: true }).fill("Stop timers");
        await page.getByLabel("Add action", { exact: true }).selectOption("timers.stop");
        await page.getByRole("button", { name: "한국어", exact: true }).click();
        await page.getByLabel("Stage name", { exact: true }).fill("출시");
        await page.getByLabel("Stage description", { exact: true }).fill("인수 완료");
        for (const [language, name, description] of [
            ["日本語", "リリース", "受入完了"],
            ["中文", "已发布", "验收完成"],
        ]) {
            await page.getByRole("button", { name: language, exact: true }).click();
            await page.getByLabel("Stage name", { exact: true }).fill(name);
            await page.getByLabel("Stage description", { exact: true }).fill(description);
        }
        await page.getByLabel("Language code", { exact: true }).fill("fr");
        await page.getByRole("button", { name: "Add language", exact: true }).click();
        await page.getByLabel("Stage name", { exact: true }).fill("Livré");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
        await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Livré");
        expect(saves).toBe(1);
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
        expect(saves).toBe(2);
        await expect(page.getByLabel("Machine key", { exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await page.reload();
        await page.getByRole("button", { name: /^Released/ }).click();
        await expect(page.getByRole("group", { name: "Action 1", exact: true })).toContainText("Stop running timers");
        await expect(page.getByLabel("Color hex code", { exact: true })).toHaveValue("#8B5CF6");
        await expect(page.getByLabel("Active queue", { exact: true })).toHaveValue("exclude");
        await page.getByRole("button", { name: "한국어", exact: true }).click();
        await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("출시");
        for (const [language, name, description] of [
            ["日本語", "リリース", "受入完了"],
            ["中文", "已发布", "验收完成"],
        ]) {
            await page.getByRole("button", { name: language, exact: true }).click();
            await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue(name);
            await expect(page.getByLabel("Stage description", { exact: true })).toHaveValue(description);
        }
        await page.getByRole("button", { name: "English", exact: true }).click();
        await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Released");
        await expect(page.getByLabel("Stage description", { exact: true })).toHaveValue("Accepted delivery");
        await page.getByRole("button", { name: "fr", exact: true }).click();
        await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Livré");
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.getByRole("button", { name: "Deactivate", exact: true }).click();
        const confirm = page.getByRole("dialog", { name: "Deactivate", exact: true });
        await expect(confirm).toBeVisible();
        await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(confirm).not.toBeVisible();
        await expect(page.getByRole("button", { name: "Deactivate", exact: true })).toBeEnabled();
        await page.getByRole("button", { name: "Deactivate", exact: true }).click();
        await confirm.getByRole("button", { name: "Deactivate", exact: true }).click();
        await expect(page.getByRole("dialog")).not.toBeVisible();
        await expect(page.getByRole("button", { name: /^Released.*Inactive/ })).toBeVisible();
    });

test("read-only stage settings prevent mutations", async ({ page }) => {
    let writes = 0;
    await page.route("**/settings/workflow-stages**", (route) => {
        const headers = {
            "Access-Control-Allow-Origin": new URL(test.info().project.use.baseURL!).origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "content-type,authorization",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (route.request().method() !== "GET") writes++;
        return route.fulfill({ headers, json: { stages: [existing()] } });
    });
    await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html?readonly=1");
    await expect(page.getByRole("button", { name: "New stage", exact: true })).not.toBeVisible();
    await page.getByRole("button", { name: /^Closed/ }).click();
    await expect(page.getByLabel("Stage name", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Deactivate", exact: true })).not.toBeVisible();
    expect(writes).toBe(0);
});

test("conflict preserves the draft and reload requires explicit discard", async ({ page }) => {
    let latest: IWorkflowStage = { ...existing(), rules: [botRule("saved-policy-bot")] };
    let writes = 0;
    await page.route("**/settings/workflow-stages**", async (route) => {
        const headers = {
            "Access-Control-Allow-Origin": new URL(test.info().project.use.baseURL!).origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET,PUT,OPTIONS",
            "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (route.request().method() === "GET") return route.fulfill({ headers, json: { stages: [latest] } });
        writes++;
        expect(requestJSON(route.request()).expected_revision).toBe("a".repeat(64));
        latest = { ...latest, name: "Latest server name", revision: "b".repeat(64) };
        return route.fulfill({ status: 409, headers, json: { code: "EX3004" } });
    });
    await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
    await page.getByRole("button", { name: /^Closed/ }).click();
    await page.getByLabel("Stage name", { exact: true }).fill("Unsaved local name");
    const policy = page.getByRole("group", { name: "Workflow rules", exact: true });
    await policy.getByRole("button", { name: "Remove rule", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Your draft is preserved");
    await expect(policy.getByRole("button", { name: "Remove rule", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Unsaved local name");
    await page.getByRole("alert").getByRole("button", { name: "Refresh", exact: true }).click();
    const confirm = page.getByRole("dialog", { name: "Discard unsaved changes?", exact: true });
    await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Unsaved local name");
    await page.getByRole("alert").getByRole("button", { name: "Refresh", exact: true }).click();
    await confirm.getByRole("button", { name: "Discard changes", exact: true }).click();
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Latest server name");
    await expect(policy.getByLabel("Bot", { exact: true })).toHaveValue("saved-policy-bot");
    await expect(page.getByRole("alert")).not.toBeVisible();
    expect(writes).toBe(1);
});

test("discard confirmation preserves draft on cancel and closes without writes on confirm", async ({ page }) => {
    let writes = 0;
    page.on("dialog", () => {
        throw new Error("Native confirmation must not open");
    });
    await page.route("**/settings/workflow-stages**", (route) => {
        const headers = {
            "Access-Control-Allow-Origin": new URL(test.info().project.use.baseURL!).origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "content-type,authorization",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (route.request().method() !== "GET") writes++;
        return route.fulfill({ headers, json: { stages: [existing()] } });
    });
    await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
    await page.getByRole("button", { name: /^Closed/ }).click();
    await page.getByLabel("Stage name", { exact: true }).fill("Unsaved name");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Discard unsaved changes?", exact: true });
    await expect(confirmation).toBeVisible();
    await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Unsaved name");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    expect(writes).toBe(0);
});

for (const width of [1440, 390]) {
    test(`common rules save compaction OR thresholds and ordered archive with human time units at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = existing();
        let writes = 0;
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [saved] } }));
        await page.route("**/settings/workflow-stages/existing", async (route) => {
            const payload = requestJSON(route.request());
            expect(payload.expected_revision).toBe(saved.revision);
            expect(Object.keys(payload).sort()).toEqual(
                [
                    "key",
                    "name",
                    "description",
                    "color",
                    "order",
                    "counts_as_completed",
                    "active_queue_policy",
                    "overdue_policy",
                    "confirm_descendant_move",
                    "translations",
                    "rules",
                    "expected_revision",
                ].sort()
            );
            expect(payload.rules).toEqual([
                {
                    name: "Compact and archive",
                    is_enabled: true,
                    trigger: { kind: "interval", interval_seconds: 150 },
                    condition_match: "any",
                    conditions: [
                        { kind: "uncompacted_characters", operator: "gte", value: 24000 },
                        { kind: "uncompacted_comments", operator: "gte", value: 25 },
                        { kind: "uncompacted_turns", operator: "gte", value: 10 },
                    ],
                    actions: [
                        { kind: "comments.compact", executor: { kind: "compactor" }, config: {}, requires_previous_success: false },
                        {
                            kind: "card.archive",
                            executor: { kind: "compactor" },
                            config: { archive_after_seconds: 7200 },
                            requires_previous_success: true,
                        },
                    ],
                },
            ]);
            saved = { ...saved, ...payload, revision: "b".repeat(64) };
            writes++;
            await route.fulfill({ json: { stage: saved } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("button", { name: "Add rule", exact: true }).click();
        await page.getByLabel("Trigger", { exact: true }).selectOption("interval");
        await page.getByLabel("Rule interval minutes", { exact: true }).fill("2.5");
        await page.getByLabel("Rule name", { exact: true }).fill("Compact and archive");
        await page.getByLabel("Add action", { exact: true }).selectOption("comments.compact");
        await expect(page.getByLabel("Condition match", { exact: true })).toHaveValue("any");
        await expect(page.getByLabel("Condition match", { exact: true })).toBeDisabled();
        await page.getByLabel("Condition value 1", { exact: true }).fill("24000");
        for (const [index, kind, value] of [
            [2, "uncompacted_comments", "25"],
            [3, "uncompacted_turns", "10"],
        ] as const) {
            await page.getByRole("button", { name: "Add condition", exact: true }).click();
            await page.getByLabel(`Condition type ${index}`, { exact: true }).selectOption(kind);
            await page.getByLabel(`Condition value ${index}`, { exact: true }).fill(value);
        }
        await page.getByLabel("Add action", { exact: true }).selectOption("card.archive");
        await page.getByLabel("Archive after (hours)", { exact: true }).fill("2");
        const archive = page.getByRole("group", { name: "Action 2", exact: true });
        await expect(archive.getByLabel("Require previous action success")).toBeChecked();
        await expect(archive.getByLabel("Require previous action success")).toBeDisabled();
        await expect(archive).toContainText("explicit current board grant");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes).toBe(1);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Condition value 1", { exact: true })).toHaveValue("24000");
        await expect(page.getByLabel("Condition value 2", { exact: true })).toHaveValue("25");
        await expect(page.getByLabel("Condition value 3", { exact: true })).toHaveValue("10");
        await expect(page.getByLabel("Rule interval minutes", { exact: true })).toHaveValue("2.5");
        await expect(page.getByLabel("Archive after (hours)", { exact: true })).toHaveValue("2");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.getByLabel("Archive after (hours)", { exact: true }).scrollIntoViewIfNeeded();
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-common-rules-${width}.png` });
    });

    test(`author notification rule saves typed conditions and reloads at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = existing();
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [saved] } }));
        await page.route("**/settings/workflow-stages/existing", async (route) => {
            const payload = requestJSON(route.request());
            expect(payload.rules).toEqual([
                {
                    name: "Notify author",
                    is_enabled: true,
                    trigger: { kind: "stage_entered" },
                    condition_match: "all",
                    conditions: [{ kind: "actor_is_not_author", operator: "eq", value: true }],
                    actions: [{ kind: "author.notify", executor: { kind: "event_actor" }, config: {}, requires_previous_success: false }],
                },
            ]);
            saved = { ...saved, ...payload, revision: "b".repeat(64) };
            await route.fulfill({ json: { stage: saved } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("button", { name: "Add rule", exact: true }).click();
        await page.getByLabel("Rule name", { exact: true }).fill("Notify author");
        await page.getByRole("button", { name: "Add condition", exact: true }).click();
        await page.getByLabel("Add action", { exact: true }).selectOption("author.notify");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved.rules.length).toBe(1);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Condition type 1", { exact: true })).toHaveValue("actor_is_not_author");
        await expect(page.getByLabel("Condition value 1", { exact: true })).toHaveValue("true");
        await expect(page.getByRole("group", { name: "Action 1", exact: true })).toContainText("Notify the author");
        await page.getByRole("dialog").screenshot({ path: `.verification/author-entry-notification-${width}.png` });
    });
}

for (const width of [1440, 390]) {
    test(`compaction workflow history shows profile and outcomes at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        const statuses = ["succeeded", "failed", "cancelled", "uncertain", "running"];
        await page.route("**/settings/workflow-stages**", (route) => {
            const url = new URL(route.request().url());
            if (!url.pathname.endsWith("/history")) return route.fulfill({ json: { stages: [existing()] } });
            expect(route.request().method()).toBe("GET");
            return route.fulfill({
                json: {
                    items: [],
                    legacy_items: statuses.map((status) => ({
                        ...effectRun(status),
                        source: status === "running" ? "continuous" : "manual",
                        actor_type: status === "running" ? "profile" : "user",
                        actor_uid: status === "running" ? "compactor" : "user",
                        status,
                        effects: ["compact_comments"],
                        result: { compacted: status === "succeeded" ? 1 : 0 },
                        profile: { uid: "compactor", display_name: "Compactor <img src=x onerror=alert(1)>" },
                        error_code: status === "failed" ? "provider_error" : null,
                    })),
                    next_cursor: null,
                },
            });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("tab", { name: "History", exact: true }).click();
        const history = page.getByRole("region", { name: "Effect history", exact: true });
        for (const label of ["Succeeded", "Failed", "Cancelled", "Uncertain", "Running"]) {
            await expect(history.getByText(label, { exact: true })).toHaveCount(1);
        }
        const failed = history.getByRole("row").filter({ hasText: "Failed" });
        await failed.getByText("Effect details", { exact: true }).click();
        await expect(failed).toContainText("Execution profile: Compactor <img src=x onerror=alert(1)>");
        await expect(failed).toContainText("Error code: provider_error");
        await expect(failed.getByText("Compact comments", { exact: true })).toBeVisible();
        await expect(history.locator("img")).toHaveCount(0);
        await expect(failed).not.toContainText("Completed items:");
        const running = history.getByRole("row").filter({ hasText: "Running" });
        await expect(running).toContainText("Continuous effects");
        await running.getByText("Effect details", { exact: true }).click();
        await expect(running.getByText("Execution profile", { exact: true })).toBeVisible();
        await expect(running.getByText("User", { exact: true })).toHaveCount(0);
        await expect(running).not.toContainText("Finished:");
        await page.getByRole("dialog").screenshot({ path: `.verification/compaction-workflow-history-${width}.png` });
    });
}

for (const width of [1440, 390]) {
    test(`root family confirmation saves and reloads at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = existing();
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [saved] } }));
        await page.route("**/settings/workflow-stages/existing", async (route) => {
            const payload = requestJSON(route.request());
            expect(payload.confirm_descendant_move).toBe(true);
            expect(payload.rules).toEqual([]);
            saved = { ...saved, ...payload, revision: "b".repeat(64) };
            await route.fulfill({ json: { stage: saved } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByLabel("Ask to move descendants when a root card enters this stage", { exact: true }).check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved.confirm_descendant_move).toBe(true);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Ask to move descendants when a root card enters this stage", { exact: true })).toBeChecked();
        await expect(page.getByRole("group", { name: /^Action [0-9]+$/ })).toHaveCount(0);
        await page.getByRole("dialog").screenshot({ path: `.verification/root-family-confirmation-${width}.png` });
    });
}

for (const choice of ["Move all descendants", "Move only this card", "Cancel"]) {
    test(`family move confirmation ${choice}`, async ({ page }) => {
        const requests: Record<string, unknown>[] = [];
        await page.route("**/board/board/card/root/order", async (route) => {
            requests.push(requestJSON(route.request()));
            await route.fulfill({ json: requests.length === 1 ? { confirmation_required: true, confirmation_revision: "revision-one" } : {} });
        });
        await page.goto("/src/pages/SettingsPage/CardFamilyMove.fixture.html");
        await page.getByRole("button", { name: "Move root", exact: true }).click();
        await expect(page.getByRole("alertdialog")).toBeVisible();
        expect(requests).toHaveLength(1);
        await page.getByRole("button", { name: choice, exact: true }).click();
        await expect(page.getByRole("alertdialog")).not.toBeVisible();
        await expect(page.locator("output")).toHaveText(choice === "Cancel" ? "cancelled" : "moved");
        expect(requests).toHaveLength(choice === "Cancel" ? 1 : 2);
        if (choice !== "Cancel") {
            expect(requests[1].move_descendants).toBe(choice === "Move all descendants");
            expect(requests[1].confirmation_revision).toBe("revision-one");
        }
    });
}

for (const width of [1440, 390]) {
    test(`workflow bot rules ${width}px preserve unavailable references, permissions and exact writes`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1100 });
        let stage: IWorkflowStage = { ...existing(), rules: [botRule("saved-missing-bot")] };
        let saved: Record<string, unknown> | undefined;
        const bot = { uid: "available-bot", name: "Available bot", entry_available: true, policy_available: true };
        const unavailable = { uid: "unavailable-bot", name: "Unavailable bot", entry_available: false, policy_available: false };
        await page.route("**/settings/workflow-stages**", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            const url = new URL(route.request().url());
            if (url.pathname.endsWith("/history")) return route.fulfill({ json: { items: [], legacy_items: [], next_cursor: null } });
            if (url.pathname.endsWith("/bot-options"))
                return route.fulfill({
                    json: {
                        bots: [bot, unavailable],
                        schedules: [
                            { uid: "active-schedule", bot_uid: bot.uid, card_uid: "card", name: "Active schedule", is_available: true },
                            { uid: "frozen-schedule", bot_uid: bot.uid, card_uid: "card", name: "Frozen schedule", is_available: false },
                        ],
                    },
                });
            if (route.request().method() === "PUT") {
                saved = requestJSON(route.request());
                stage = { ...stage, ...saved, revision: "b".repeat(64) };
                return route.fulfill({ json: { stage } });
            }
            return route.fulfill({ json: { stages: [stage] } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Bot", { exact: true })).toHaveValue("saved-missing-bot");
        await expect(page.getByLabel("Bot", { exact: true }).locator("option[value='unavailable-bot']")).toHaveJSProperty("disabled", true);
        await page.getByRole("tab", { name: "History", exact: true }).click();
        await page.getByRole("tab", { name: "Settings", exact: true }).click();
        await expect(page.getByLabel("Bot", { exact: true })).toHaveValue("saved-missing-bot");
        await page.getByRole("button", { name: "Add rule", exact: true }).click();
        const rule = page.getByRole("group", { name: "Rule 2", exact: true });
        await rule.getByLabel("Rule name", { exact: true }).fill("Scheduled bot");
        await rule.getByLabel("Trigger", { exact: true }).selectOption("interval");
        await rule.getByLabel("Rule interval minutes", { exact: true }).fill("2.5");
        await rule.getByLabel("Add action", { exact: true }).selectOption("bot.invoke");
        await rule.getByLabel("Bot", { exact: true }).selectOption(bot.uid);
        await expect(rule.getByLabel("Card schedule", { exact: true })).toHaveValue("active-schedule");
        await expect(rule.getByLabel("Card schedule", { exact: true }).locator("option[value='frozen-schedule']")).toHaveJSProperty("disabled", true);
        await expect(rule).toContainText("independent eligibility checks");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved).toBeTruthy();
        expect(saved?.expected_revision).toBe("a".repeat(64));
        const writtenRules = saved?.rules as IWorkflowRule[];
        expect(writtenRules[0]).toEqual({ ...botRule("saved-missing-bot"), revision: undefined });
        expect(Object.keys(writtenRules[0])).not.toContain("revision");
        expect(writtenRules[1].trigger).toEqual({ kind: "interval", interval_seconds: 150 });
        expect(writtenRules[1].actions).toEqual([
            {
                kind: "bot.invoke",
                executor: { kind: "bot", uid: bot.uid },
                config: { bot_uid: bot.uid, schedule_uid: "active-schedule" },
                requires_previous_success: false,
            },
        ]);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByRole("group", { name: "Rule 1", exact: true }).getByLabel("Bot", { exact: true })).toHaveValue("saved-missing-bot");
        await expect(rule.getByLabel("Rule interval minutes", { exact: true })).toHaveValue("2.5");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-bot-bindings-${width}.png` });
        await page.evaluate(() => document.documentElement.classList.add("dark"));
        const background = await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor);
        await expect(page.getByRole("dialog")).toHaveCSS("background-color", background);
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-bot-bindings-${width}-dark.png` });
    });

    test(`workflow bot rules ${width}px preserve unavailable saved references when options fail`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1100 });
        let stage: IWorkflowStage = { ...existing(), rules: [botRule("saved-missing-bot")] };
        let writes = 0;
        await page.route("**/settings/workflow-stages**", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            if (new URL(route.request().url()).pathname.endsWith("/bot-options")) return route.fulfill({ status: 503, json: {} });
            if (route.request().method() === "PUT") {
                const payload = requestJSON(route.request());
                expect(payload.rules[0].actions[0].config.bot_uid).toBe("saved-missing-bot");
                expect(payload.rules[0].uid).toBe("saved-rule");
                expect(payload.rules[0].actions[0].uid).toBe("saved-action");
                stage = { ...stage, ...payload, revision: "b".repeat(64) };
                writes++;
                return route.fulfill({ json: { stage } });
            }
            return route.fulfill({ json: { stages: [stage] } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByRole("status")).toHaveText("Bot options unavailable. Saved bindings are preserved.");
        await expect(page.getByLabel("Bot", { exact: true })).toBeDisabled();
        await expect(page.getByLabel("Bot", { exact: true })).toHaveValue("saved-missing-bot");
        await page.getByLabel("Stage description", { exact: true }).fill("Updated description");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes).toBe(1);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Bot", { exact: true })).toHaveValue("saved-missing-bot");
    });
}

for (const width of [1440, 390]) {
    test(`common rule history ${width}px shows revisions, actors, executors, outcomes and preserved evidence`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        let writes = 0;
        let stage: IWorkflowStage = { ...existing(), rules: [{ ...botRule("current-bot"), uid: "rule-history", revision: 18 }] };
        const historicalSnapshot = {
            ...botRule("bot-history"),
            uid: "rule-history",
            stage_key: "closed",
            name: "Original admitted rule",
            revision: 17,
            conditions: [{ kind: "uncompacted_comments", operator: "gte", value: 10 }],
            actions: [{ ...botRule("bot-history").actions[0], uid: "action-history" }],
        };
        const statuses = ["queued", "running", "accepted", "succeeded", "failed", "skipped", "cancelled", "uncertain"];
        await page.route("**/settings/workflow-stages**", (route) => {
            if (route.request().method() === "PUT") {
                writes++;
                stage = { ...stage, ...requestJSON(route.request()), revision: "b".repeat(64) };
                return route.fulfill({ json: { stage } });
            }
            if (!new URL(route.request().url()).pathname.endsWith("/history")) return route.fulfill({ json: { stages: [stage] } });
            return route.fulfill({
                json: {
                    items: statuses.map((status) => ({
                        uid: status,
                        rule_uid: "rule-history",
                        rule_revision: 17,
                        configured_by_uid: status === "skipped" ? null : "settings-author-history",
                        rule_snapshot: historicalSnapshot,
                        event_id: `event-${status}`,
                        event_type: "card_comment_added",
                        occurred_at: "2026-10-24T03:00:00Z",
                        actor_uid: "actor-history",
                        actor_type: "user",
                        project_uid: "board-source",
                        card_uid: "card-source",
                        residency: "2026-10-23T03:00:00Z",
                        condition_results: [{ kind: "uncompacted_comments", matched: true }],
                        status,
                        actions: [
                            {
                                uid: `action-run-${status}`,
                                action_uid: "action-history",
                                kind: "bot.invoke",
                                executor_uid: "bot-history",
                                executor_type: "bot",
                                status,
                                attempt: 2,
                                attempts: [
                                    {
                                        attempt: 1,
                                        status: "failed",
                                        started_at: "2026-10-24T02:59:00Z",
                                        finished_at: "2026-10-24T02:59:01.250Z",
                                        error_code: "transient_provider_error",
                                    },
                                    ...(status === "succeeded"
                                        ? [
                                              {
                                                  attempt: 2,
                                                  status,
                                                  started_at: "2026-10-24T03:00:00Z",
                                                  finished_at: "2026-10-24T03:01:00Z",
                                                  error_code: null,
                                              },
                                          ]
                                        : []),
                                ],
                                idempotency_key: `idempotency-${status}`,
                                result: { bot_log_uid: "log-history", note: "<img src=x onerror=alert(1)>" },
                                error_code: status === "failed" ? "provider_error" : null,
                                started_at: "2026-10-24T03:00:00Z",
                                finished_at: status === "succeeded" ? "2026-10-24T03:01:00Z" : null,
                            },
                        ],
                    })),
                    legacy_items: [effectRun("legacy-history")],
                    next_cursor: null,
                },
            });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("tab", { name: "History", exact: true }).click();
        const history = page.getByRole("region", { name: "Effect history", exact: true });
        const runs = history.locator("article");
        await expect(runs).toHaveCount(8);
        for (const status of ["Queued", "Running", "Dispatch accepted", "Succeeded", "Failed", "Skipped", "Cancelled", "Uncertain"])
            await expect(runs.getByText(status, { exact: true })).toHaveCount(1);
        const accepted = runs.filter({ hasText: "event-accepted" });
        await accepted.getByText("Rule details", { exact: true }).click();
        await expect(accepted).toContainText("Rule: Original admitted rule (rule-history)");
        await expect(accepted).toContainText("Revision: 17");
        await expect(accepted).toContainText("Actor: user actor-history");
        await expect(accepted).toContainText("Settings author: settings-author-history");
        await expect(runs.filter({ hasText: "event-skipped" })).toContainText("Settings author: Unknown settings author");
        const snapshot = accepted.getByRole("region", { name: "Historical rule snapshot", exact: true });
        await expect(snapshot).toBeVisible();
        expect(JSON.parse(await snapshot.locator("pre").innerText())).toEqual(historicalSnapshot);
        await expect(accepted).toContainText("Executor: Bot bot-history");
        await expect(accepted).toContainText("Invoke bot · Dispatch accepted");
        await expect(accepted).toContainText("Attempt: 2");
        const attempts = accepted.getByRole("region", { name: "Attempt history", exact: true });
        await expect(attempts).toContainText("Attempt: 1");
        await expect(attempts).toContainText("Failed");
        await expect(attempts).toContainText("Duration: 1.25 sec");
        await expect(attempts).toContainText("transient_provider_error");
        await expect(accepted.getByText("Duration: Duration unavailable", { exact: true })).toBeVisible();
        const succeeded = runs.filter({ hasText: "event-succeeded" });
        await succeeded.getByText("Rule details", { exact: true }).click();
        await expect(succeeded.getByText("Duration: 60 sec", { exact: true })).toHaveCount(2);
        await expect(succeeded.getByRole("region", { name: "Attempt history", exact: true }).getByRole("listitem")).toHaveCount(2);
        await expect(accepted).toContainText("log-history");
        await expect(accepted).toContainText("Dispatch accepted; see bot logs for execution details.");
        await expect(accepted).not.toContainText("Succeeded");
        await expect(history.locator("img")).toHaveCount(0);
        await expect(history.getByText("Preserved historical effects (read only)", { exact: true })).toBeVisible();
        await expect(accepted.getByRole("link", { name: "Open card" })).toHaveAttribute("href", "/board/board-source/card-source");
        expect(writes).toBe(0);
        await page.getByRole("tab", { name: "Settings", exact: true }).click();
        await page.getByLabel("Rule name", { exact: true }).fill("Current modified rule");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes).toBe(1);
        expect(stage.rules[0].name).toBe("Current modified rule");
        expect(stage.rules[0].uid).toBe(historicalSnapshot.uid);
        await page.getByRole("tab", { name: "History", exact: true }).click();
        await accepted.getByText("Rule details", { exact: true }).click();
        await expect(snapshot).toBeVisible();
        expect(JSON.parse(await snapshot.locator("pre").innerText())).toEqual(historicalSnapshot);
        await expect(snapshot).not.toContainText("Current modified rule");
        await expect(accepted).toContainText("Settings author: settings-author-history");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-common-history-${width}.png` });
    });

    test(`common rule editor ${width}px preserves action identities and enforces compaction safety on reorder`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        let saved: IWorkflowStage | undefined;
        const stage = {
            ...existing(),
            rules: [
                {
                    uid: "rule-ordered",
                    revision: 3,
                    name: "Ordered rule",
                    is_enabled: true,
                    trigger: { kind: "stage_entered" as const },
                    condition_match: "any" as const,
                    conditions: [{ kind: "uncompacted_comments" as const, operator: "gte" as const, value: 10 }],
                    actions: [
                        {
                            uid: "compact-action",
                            kind: "comments.compact" as const,
                            executor: { kind: "compactor" as const },
                            config: {},
                            requires_previous_success: false,
                        },
                        {
                            uid: "archive-action",
                            kind: "card.archive" as const,
                            executor: { kind: "compactor" as const },
                            config: { archive_after_seconds: 7200 },
                            requires_previous_success: true,
                        },
                    ],
                },
            ],
        };
        await page.route("**/settings/workflow-stages**", (route) => {
            if (route.request().method() === "PUT") {
                saved = { ...stage, ...requestJSON(route.request()) };
                return route.fulfill({ json: { stage: saved } });
            }
            return route.fulfill({ json: { stages: [stage] } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByRole("group", { name: "Action 2", exact: true }).getByRole("button", { name: "Move action up", exact: true }).click();
        await expect(page.getByRole("group", { name: "Action 1", exact: true })).toContainText("Auto archive cards");
        await expect(page.getByRole("group", { name: "Action 1", exact: true }).getByLabel("Require previous action success")).not.toBeChecked();
        await page.getByRole("group", { name: "Action 1", exact: true }).getByRole("button", { name: "Move action down", exact: true }).click();
        await expect(page.getByRole("group", { name: "Action 2", exact: true }).getByLabel("Require previous action success")).toBeChecked();
        await page.getByRole("button", { name: "Remove condition", exact: true }).click();
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
        await expect(page.getByRole("alert")).toContainText("positive OR threshold");
        await page.getByRole("button", { name: "Add condition", exact: true }).click();
        await page.getByLabel("Trigger", { exact: true }).selectOption("event");
        await page.getByLabel("Event type", { exact: true }).selectOption(DomainEventType.CardUpdated);
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved).toBeTruthy();
        expect(saved?.rules[0].trigger).toEqual({ kind: "event", event_type: "card_updated" });
        expect(saved?.rules[0].actions.map((action) => action.uid)).toEqual(["compact-action", "archive-action"]);
        expect(saved?.rules[0].actions.map((action) => action.requires_previous_success)).toEqual([false, true]);
        expect(Object.keys(saved?.rules[0] ?? {})).not.toContain("revision");
    });
}

for (const width of [1440, 390]) {
    for (const [locale, stageName, rulesLabel, addRule, triggerLabel, ruleName, actionLabel, compactLabel, matchLabel, anyLabel] of [
        [
            "en-US",
            "Closed",
            "Workflow rules",
            "Add rule",
            "Trigger",
            "Rule name",
            "Add action",
            "Compact comments",
            "Condition match",
            "Any condition",
        ],
        ["ko-KR", "완료", "워크플로우 규칙", "규칙 추가", "트리거", "규칙 이름", "액션 추가", "댓글 압축", "조건 결합", "하나 이상 충족"],
        [
            "ja-JP",
            "完了",
            "ワークフロールール",
            "ルールを追加",
            "トリガー",
            "ルール名",
            "アクションを追加",
            "コメントを圧縮",
            "条件の組み合わせ",
            "いずれかを満たす",
        ],
        ["zh-CN", "已完成", "工作流规则", "添加规则", "触发器", "规则名称", "添加操作", "压缩评论", "条件匹配", "满足任一条件"],
    ]) {
        test(`common rules ${locale} ${width}px localize editor and fit viewport`, async ({ page }) => {
            await page.setViewportSize({ width, height: 1000 });
            await page.addInitScript((language) => localStorage.setItem("lang", language), locale);
            await page.route("**/settings/workflow-stages**", (route) => route.fulfill({ json: { stages: [existing()] } }));
            await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
            await page.getByRole("button", { name: new RegExp(`^${stageName}`) }).click();
            const rules = page.getByRole("group", { name: rulesLabel, exact: true });
            await rules.getByRole("button", { name: addRule, exact: true }).click();
            await rules.getByLabel(ruleName, { exact: true }).fill("Localized rule");
            await rules.getByLabel(triggerLabel, { exact: true }).selectOption("event");
            await rules.getByLabel(actionLabel, { exact: true }).selectOption("comments.compact");
            await expect(rules.getByLabel(matchLabel, { exact: true })).toHaveValue("any");
            await expect(rules.getByLabel(matchLabel, { exact: true }).locator("option:checked")).toHaveText(anyLabel);
            await expect(rules.getByRole("group", { name: /1$/, exact: false }).last()).toContainText(compactLabel);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await rules.getByLabel(actionLabel, { exact: true }).scrollIntoViewIfNeeded();
            await page.getByRole("dialog").screenshot({ path: `.verification/workflow-common-${locale}-${width}.png` });
        });
    }
}

for (const eventType of [DomainEventType.CardDeleted, DomainEventType.BotCreated, DomainEventType.ProjectUpdated]) {
    test(`workflow event catalog refuses loaded unsupported ${eventType} until repaired`, async ({ page }) => {
        let writes = 0;
        let saved: IWorkflowStage | undefined;
        const stage: IWorkflowStage = {
            ...existing(),
            rules: [
                {
                    ...botRule("unused"),
                    trigger: { kind: "event", event_type: eventType },
                    actions: [{ kind: "timers.stop", executor: { kind: "event_actor" }, config: {}, requires_previous_success: false }],
                },
            ],
        };
        await page.route("**/settings/workflow-stages**", (route) => {
            if (route.request().method() === "PUT") {
                writes++;
                saved = { ...stage, ...requestJSON(route.request()) };
                return route.fulfill({ json: { stage: saved } });
            }
            return route.fulfill({ json: { stages: [stage] } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        const eventSelect = page.getByLabel("Event type", { exact: true });
        for (const unsupported of Object.values(DomainEventType).filter(
            (value) => !value.startsWith("card_") || value === DomainEventType.CardDeleted
        )) {
            await expect(eventSelect.locator(`option[value="${unsupported}"]`)).toHaveCount(0);
        }
        for (const supported of [DomainEventType.CardUpdated, DomainEventType.CardCommentDeleted, DomainEventType.CardCheckitemDeleted]) {
            await expect(eventSelect.locator(`option[value="${supported}"]`)).toHaveCount(1);
        }
        await page.getByLabel("Rule name", { exact: true }).fill("Repaired event rule");
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
        await expect(page.getByRole("alert")).toContainText("Each rule requires");
        await page.locator("form").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
        expect(writes).toBe(0);
        await eventSelect.selectOption(DomainEventType.CardUpdated);
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes).toBe(1);
        expect(saved?.rules[0].trigger).toEqual({ kind: "event", event_type: DomainEventType.CardUpdated });
    });
}
