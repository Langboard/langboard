import { test, expect } from "@playwright/test";
import type { IWorkflowStage } from "@/controllers/api/settings/workflowStages/useWorkflowStages";

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
    entry_effects: [],
    translations: {
        en: { name: "Closed", description: "Accepted work" },
        ko: { name: "완료", description: "완료 업무" },
        ja: { name: "完了", description: "完了業務" },
        zh: { name: "已完成", description: "已完成工作" },
    },
});

const effectRun = (uid: string, started = "2026-10-21T03:00:00Z") => ({
    uid,
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
                    ? { items: [effectRun("one"), effectRun("two")], next_cursor: null }
                    : { items: [effectRun("one")], next_cursor: "one" },
            });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByLabel("Stage name", { exact: true }).fill("Unsaved stage");
        await page.getByRole("tab", { name: "History", exact: true }).click();
        const history = page.getByRole("region", { name: "Effect history", exact: true });
        await expect(history.getByText("Succeeded", { exact: true })).toHaveCount(1);
        await history.getByText("Effect details", { exact: true }).click();
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
                    items: [effectRun("run", url.pathname.includes("/existing/") ? "2020-01-01T00:00:00Z" : "2026-10-21T03:00:00Z")],
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
                json: new URL(route.request().url()).pathname.endsWith("/history") ? { items: [], next_cursor: null } : { stages: [existing()] },
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
                "Access-Control-Allow-Origin": "http://127.0.0.1:4194",
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
            const stage = { ...route.request().postDataJSON(), revision: "b".repeat(64), uid: "saved", is_active: true, is_builtin: false };
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
        await page.getByLabel("Stop running timers", { exact: true }).check();
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
        await expect(page.getByLabel("Stop running timers", { exact: true })).toBeChecked();
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
            "Access-Control-Allow-Origin": "http://127.0.0.1:4194",
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
    let latest = existing();
    let writes = 0;
    await page.route("**/settings/workflow-stages**", async (route) => {
        const headers = {
            "Access-Control-Allow-Origin": "http://127.0.0.1:4194",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET,PUT,OPTIONS",
            "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (route.request().method() === "GET") return route.fulfill({ headers, json: { stages: [latest] } });
        writes++;
        expect(route.request().postDataJSON().expected_revision).toBe("a".repeat(64));
        latest = { ...latest, name: "Latest server name", revision: "b".repeat(64) };
        return route.fulfill({ status: 409, headers, json: { code: "EX3004" } });
    });
    await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
    await page.getByRole("button", { name: /^Closed/ }).click();
    await page.getByLabel("Stage name", { exact: true }).fill("Unsaved local name");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Your draft is preserved");
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Unsaved local name");
    await page.getByRole("alert").getByRole("button", { name: "Refresh", exact: true }).click();
    const confirm = page.getByRole("dialog", { name: "Discard unsaved changes?", exact: true });
    await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Unsaved local name");
    await page.getByRole("alert").getByRole("button", { name: "Refresh", exact: true }).click();
    await confirm.getByRole("button", { name: "Discard changes", exact: true }).click();
    await expect(page.getByLabel("Stage name", { exact: true })).toHaveValue("Latest server name");
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
            "Access-Control-Allow-Origin": "http://127.0.0.1:4194",
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
    test(`continuous effects save and reload with human time units at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = {
            ...existing(),
            continuous_effects: { auto_compact: false, auto_archive: false, interval_seconds: 300, archive_after_seconds: 86400 },
        };
        let writes = 0;
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [saved] } }));
        await page.route("**/settings/workflow-stages/existing", async (route) => {
            writes += 1;
            const payload = route.request().postDataJSON();
            expect(payload.expected_revision).toBe(saved.revision);
            expect(payload.continuous_effects).toEqual({
                bots: [],
                auto_compact: true,
                auto_archive: true,
                interval_seconds: 120,
                archive_after_seconds: 7200,
                compact_after_characters: 24000,
            });
            saved = { ...saved, ...payload, revision: "b".repeat(64) };
            await route.fulfill({ json: { stage: saved } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByLabel("Auto compact comments", { exact: true }).check();
        await expect(page.getByLabel("Compact after characters", { exact: true })).toHaveValue("12000");
        await page.getByLabel("Compact after characters", { exact: true }).fill("24000");
        await page.getByLabel("Auto archive cards", { exact: true }).check();
        await page.getByLabel("Check interval (minutes)", { exact: true }).fill("2");
        await page.getByLabel("Archive after (hours)", { exact: true }).fill("2");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes).toBe(1);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Auto compact comments", { exact: true })).toBeChecked();
        await expect(page.getByLabel("Auto archive cards", { exact: true })).toBeChecked();
        await expect(page.getByLabel("Compact after characters", { exact: true })).toHaveValue("24000");
        await expect(page.getByLabel("Check interval (minutes)", { exact: true })).toHaveValue("2");
        await expect(page.getByLabel("Archive after (hours)", { exact: true })).toHaveValue("2");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

for (const width of [1440, 390]) {
    test(`author entry notification saves and reloads at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = existing();
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [saved] } }));
        await page.route("**/settings/workflow-stages/existing", async (route) => {
            const payload = route.request().postDataJSON();
            expect(payload.entry_effects).toEqual(["notify_author_on_entry"]);
            saved = { ...saved, ...payload, revision: "b".repeat(64) };
            await route.fulfill({ json: { stage: saved } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByLabel("Notify the author when someone else enters this stage", { exact: true }).check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved.entry_effects).toEqual(["notify_author_on_entry"]);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Notify the author when someone else enters this stage", { exact: true })).toBeChecked();
        await expect(page.getByLabel("Complete unchecked items", { exact: true })).not.toBeChecked();
        await expect(page.getByLabel("Stop running timers", { exact: true })).not.toBeChecked();
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
                    items: statuses.map((status) => ({
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
            const payload = route.request().postDataJSON();
            expect(payload.entry_effects).toEqual(["confirm_descendant_move"]);
            saved = { ...saved, ...payload, revision: "b".repeat(64) };
            await route.fulfill({ json: { stage: saved } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await page.getByLabel("Ask to move descendants when a root card enters this stage", { exact: true }).check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved.entry_effects).toEqual(["confirm_descendant_move"]);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Ask to move descendants when a root card enters this stage", { exact: true })).toBeChecked();
        await expect(page.getByLabel("Complete unchecked items", { exact: true })).not.toBeChecked();
        await expect(page.getByLabel("Stop running timers", { exact: true })).not.toBeChecked();
        await page.getByRole("dialog").screenshot({ path: `.verification/root-family-confirmation-${width}.png` });
    });
}

for (const choice of ["Move all descendants", "Move only this card", "Cancel"]) {
    test(`family move confirmation ${choice}`, async ({ page }) => {
        const requests: Record<string, unknown>[] = [];
        await page.route("**/board/board/card/root/order", async (route) => {
            requests.push(route.request().postDataJSON());
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
    test(`workflow bot bindings ${width}px persist native triggers and per-binding interval`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1100 });
        let stage = existing();
        let saved: Record<string, unknown> | undefined;
        const bot = { uid: "123456789ab", name: "Workflow assistant", entry_available: true };
        const unavailable = { uid: "123456789ac", name: "Unavailable assistant", entry_available: false };
        await page.route("**/settings/workflow-stages**", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            const url = new URL(route.request().url());
            if (url.pathname.endsWith("/bot-options"))
                return route.fulfill({
                    json: {
                        bots: [bot, unavailable],
                        schedules: [{ uid: "123456789ad", bot_uid: bot.uid, card_uid: "card", name: "Delivery card", is_available: true }],
                    },
                });
            if (url.pathname.endsWith("/history"))
                return route.fulfill({
                    json: {
                        items: [
                            {
                                ...effectRun("bot-run"),
                                source: "bot_entry",
                                status: "accepted",
                                actor_type: "bot",
                                effects: [`bot:${bot.uid}`, "card_moved"],
                                result: { accepted: 1 },
                            },
                        ],
                        next_cursor: null,
                    },
                });
            if (route.request().method() === "PUT") {
                saved = route.request().postDataJSON();
                stage = { ...stage, ...saved, revision: "b".repeat(64) } as IWorkflowStage;
                return route.fulfill({ json: { stage } });
            }
            return route.fulfill({ json: { stages: [stage] } });
        });
        await page.goto("/src/pages/SettingsPage/WorkflowStages.fixture.html");
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Add entry bot").getByRole("option", { name: /Unavailable assistant/ })).toHaveAttribute("disabled", "");
        await page.getByLabel("Add entry bot").selectOption(bot.uid);
        await page.getByLabel("Add interval bot").selectOption(bot.uid);
        await expect(page.getByLabel("Card schedule 1", { exact: true })).toHaveValue("123456789ad");
        await page.getByLabel("Bot interval minutes").fill("2.5");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => saved?.entry_bots).toEqual([{ bot_uid: bot.uid, condition: "card_moved" }]);
        expect((saved?.continuous_effects as { bots: unknown[] }).bots).toEqual([
            {
                bot_uid: bot.uid,
                condition: "bot_cron_scheduled",
                schedule_uid: "123456789ad",
                interval_seconds: 150,
            },
        ]);
        await page.reload();
        await page.getByRole("button", { name: /^Closed/ }).click();
        await expect(page.getByLabel("Bot interval minutes")).toHaveValue("2.5");
        await page.getByLabel("Bot interval minutes").scrollIntoViewIfNeeded();
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-bot-bindings-${width}.png` });
        await page.evaluate(() => document.documentElement.classList.add("dark"));
        const darkBackground = await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor);
        await expect(page.getByRole("dialog")).toHaveCSS("background-color", darkBackground);
        await page.getByRole("dialog").screenshot({ path: `.verification/workflow-bot-bindings-${width}-dark.png` });
        await page.getByRole("tab", { name: "History", exact: true }).click();
        await expect(page.getByRole("region", { name: "Effect history" }).getByText("Dispatch accepted", { exact: true })).toBeVisible();
        await page.getByText("Effect details", { exact: true }).click();
        await expect(page.getByText("Dispatch accepted; see bot logs for execution details.", { exact: true })).toBeVisible();
    });
}
