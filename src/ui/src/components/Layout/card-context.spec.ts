import { expect, test, type Page } from "@playwright/test";

const lensCard = (uid = "fixture-card") => ({
    uid,
    project_uid: "fixture",
    project_column_uid: "column",
    title: uid === "fixture-card" ? "Lens card" : "Other lens card",
    order: 0,
    created_at: "2026-10-18T00:00:00Z",
    updated_at: "2026-10-18T00:00:00Z",
    source_type: "task",
    visibility: "SHARED",
    project_column_name: "Doing",
    member_uids: [],
    project_members: [],
    labels: [],
    relationships: [],
    current_auth_role_actions: ["read"],
    count_comment: 0,
    can_delete: false,
    can_change_visibility: false,
    is_check_card: false,
    completed: false,
    work_state: {
        version: 1,
        workflow_stage: "active",
        verification_state: "stale",
        verification_source_change_seq: 8,
        verification: {
            uid: "verification",
            source_change_seq: 7,
            decision: "partial",
            recorded_at: "2026-10-18T00:00:00Z",
            recorded_by_user_uid: null,
            recorded_by_bot_uid: null,
            evidence: [{ reference: "source-test.log", source_revision: "abc123", environment: "local-only", checkitem_uid: "acceptance" }],
            required_checkitem_uids: ["acceptance", "missing"],
        },
        execution_generation: 7,
        execution_state: null,
        blocker_state: null,
        material_kind: "work",
        lifecycle: "active",
        active_queue_eligible: null,
        checklist_progress: { total: 2, completed: 1 },
        reasons: [{ code: "verification_stale", message: "Raw private reason must not be shown", source_ref: "secret" }],
        state_inconsistency: [],
    },
});

for (const width of [1440, 320, 390]) {
    for (const [lang, labels] of Object.entries({
        "en-US": ["Execution", "Review", "History", "Unknown", "Stale", "Actions", "Close"],
        "ko-KR": ["실행", "검토", "기록", "알 수 없음", "오래됨", "작업", "닫기"],
        "ja-JP": ["実行", "レビュー", "履歴", "不明", "古い検証", "操作", "閉じる"],
        "zh-CN": ["执行", "审核", "历史", "未知", "已过期", "操作", "关闭"],
    })) {
        test(`card context actions lazily read receipts and support keyboard at ${width}px ${lang}`, async ({ page }) => {
            const errors: string[] = [];
            page.on("pageerror", (error) => errors.push(error.message));
            await page.setViewportSize({ width, height: 844 });
            const requests: string[][] = [];
            const extra: string[] = [];
            await page.route(/\/board\/fixture\/card\/[^/?]+\?sections=/, async (route) => {
                const sections = new URL(route.request().url()).searchParams.get("sections")!.split(",");
                requests.push(sections);
                const result: Record<string, unknown> = { card: lensCard(), whispers_allowed: false };
                for (const section of sections) if (section !== "description") result[section] = [];
                if (sections.includes("execution_receipts"))
                    result.execution_receipts = [6, 7].map((generation) => ({
                        generation,
                        created_at: "2026-10-18T00:00:00Z",
                        checklist_projection: [],
                        receipt: { status: "completed", summary: `Generation ${generation} evidence`, artifacts: [], checklist_evidence: [] },
                    }));
                if (sections.includes("description"))
                    (result.card as Record<string, unknown>).description = { content: "History description", type: "text" };
                await route.fulfill({ json: result });
            });
            await page.route("**/board/fixture/card/*/apps/**", (route) => {
                extra.push(route.request().url());
                return route.fulfill({ json: { items: [], executions: [], next_cursor: null } });
            });
            await page.route("**/board/fixture/card/*/seen", (route) => route.fulfill({ json: { card_uid: "fixture-card", seen_change_seq: 0 } }));
            await page.route(/^http:\/\/localhost:5381\/activity\//, (route) => route.fulfill({ json: { records: [], next_cursor: null } }));
            await page.goto(`/src/components/Layout/workbench-shell.fixture.html?lenses=1&lang=${lang}`);
            await expect.poll(() => requests.length, { timeout: 20000 }).toBeGreaterThan(0);
            await expect(page.getByRole("region", { name: "Lens card", exact: true })).toContainText("History description");
            await expect(page.getByRole("tablist")).toHaveCount(0);
            expect(requests).toHaveLength(1);
            expect(requests[0]).toContain("description");
            expect(requests[0]).not.toContain("execution_receipts");
            expect(extra).toHaveLength(0);
            await expect(page.getByText("Raw private reason must not be shown")).toHaveCount(0);
            await page.getByRole("button", { name: labels[5], exact: true }).click();
            for (const [index, title] of labels.slice(0, 3).entries()) {
                const trigger = page.getByRole("button", { name: title, exact: true });
                await trigger.focus();
                await page.keyboard.press("Enter");
                const dialog = page.getByRole("dialog", { name: title, exact: true });
                await expect(dialog).toBeVisible();
                if (index === 0) {
                    await expect(dialog).toContainText(labels[3]);
                    await expect(dialog).toContainText("Generation 7 evidence");
                    await expect(dialog).not.toContainText("Generation 6 evidence");
                } else if (index === 1) {
                    await expect(dialog).toContainText(labels[4]);
                    await expect(dialog).toContainText("abc123");
                    await expect(dialog).toContainText("local-only");
                } else await expect(dialog).toContainText("Generation 6 evidence");
                await page.keyboard.press("Escape");
                await expect(dialog).toHaveCount(0);
                await expect(trigger).toBeFocused();
            }
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await expect(page.locator("iframe")).toHaveCount(0);
            expect(errors).toEqual([]);
        });
    }
}

async function prepareDelayedLens(
    page: Page,
    { editable = false, unread = false, failSave = false, unknown = false, deadline = false, delay = false } = {}
) {
    const reads: string[] = [];
    const writes: unknown[] = [];
    let releaseHistory: (() => void) | undefined;
    let delayed = true;
    let historyReleased = !delay;
    await page.route("http://localhost:5381/**", async (route) => {
        const url = new URL(route.request().url());
        const path = url.pathname;
        reads.push(path);
        if (/\/card\/[^/]+$/.test(path) && route.request().method() === "GET") {
            const sections = url.searchParams.get("sections")!.split(",");
            if (sections.includes("description") && delayed) {
                delayed = false;
                if (!historyReleased)
                    await new Promise<void>((resolve) => {
                        releaseHistory = resolve;
                    });
            }
            const uid = path.split("/").at(-1)!;
            const card = {
                ...lensCard(uid),
                work_state: unknown ? undefined : lensCard(uid).work_state,
                deadline_at: deadline ? "2030-06-15T12:00:00Z" : null,
                current_auth_role_actions: editable ? ["*"] : ["read"],
                count_comment: 2,
                last_change_seq: 13,
                has_unread_change: unread,
                last_change_target_type: "description",
                last_change_target_uid: null,
            };
            const data: Record<string, unknown> = { card, whispers_allowed: false };
            for (const section of sections) if (section !== "description") data[section] = [];
            if (sections.includes("description"))
                (data.card as Record<string, unknown>).description = {
                    content: uid === "fixture-card" ? "Delayed history description" : "Other card description",
                };
            await route.fulfill({ json: data }).catch(() => {});
        } else if (path.endsWith("/seen")) {
            await route.fulfill({ json: { card_uid: "fixture-card", seen_change_seq: 13 } });
        } else if (path.endsWith("/details") && route.request().method() === "PUT") {
            writes.push(route.request().postDataJSON());
            await route.fulfill({ status: failSave ? 500 : 200, json: {} });
        } else if (path.endsWith("/comments")) {
            await route.fulfill({ json: { comments: [] } });
        } else await route.fulfill({ json: { items: [], records: [], next_cursor: null } });
    });
    return {
        reads,
        writes,
        release: () => {
            historyReleased = true;
            releaseHistory?.();
        },
    };
}

for (const width of [1440, 390])
    test(`comment controls and delayed outline navigation remain usable at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const fixture = await prepareDelayedLens(page, { delay: true });
        const timelineReads: string[] = [];
        let commentContent = "Initial timeline comment";
        await page.route("http://localhost:5381/activity/project/fixture/card/fixture-card/timeline?*", async (route) => {
            timelineReads.push(route.request().url());
            const timestamp = "2026-10-18T00:00:00Z";
            await route.fulfill({
                json: {
                    records: [
                        {
                            kind: "comment",
                            uid: "timeline-comment",
                            evidence_ref: "comment:timeline-comment",
                            created_at: timestamp,
                            data: {
                                uid: "timeline-comment",
                                card_uid: "fixture-card",
                                created_at: timestamp,
                                updated_at: timestamp,
                                content: { content: commentContent },
                                is_edited: false,
                                reactions: {},
                                user: {
                                    uid: "comment-author",
                                    firstname: "Comment",
                                    lastname: "Author",
                                    created_at: timestamp,
                                    updated_at: timestamp,
                                },
                            },
                        },
                    ],
                    next_cursor: null,
                },
            });
        });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
        await expect(page.locator("[data-card-loading]")).toBeVisible();
        expect(fixture.reads.filter((path) => path.endsWith("/comments"))).toEqual([]);
        expect(timelineReads).toEqual([]);
        fixture.release();
        await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
        const comments = page.getByRole("button", { name: "Comments", exact: true });
        await expect(comments).toHaveAttribute("aria-pressed", "true");
        const renderedComment = page.locator("[data-card-comment-uid=timeline-comment]");
        await expect(renderedComment).toContainText("Initial timeline comment");
        expect(timelineReads).toHaveLength(1);
        // Description anchors use /comments; the visible comment list uses /timeline.
        await expect.poll(() => fixture.reads.filter((path) => path.endsWith("/comments")).length).toBe(1);
        await comments.click();
        await expect(comments).toHaveAttribute("aria-pressed", "false");
        await expect(renderedComment).toHaveCount(0);
        expect(timelineReads).toHaveLength(1);
        commentContent = "Fresh timeline comment after reopening";
        await comments.click();
        await expect(comments).toHaveAttribute("aria-pressed", "true");
        await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
        await expect(renderedComment).toHaveCount(1);
        await expect(renderedComment).toContainText("Fresh timeline comment after reopening");
        await expect(renderedComment).not.toContainText("Initial timeline comment");
        expect(timelineReads).toHaveLength(2);
        await page.evaluate(() =>
            window.dispatchEvent(
                new CustomEvent("langboard:open-card-outline-section", { detail: { cardUID: "fixture-card", section: "description" } })
            )
        );
        fixture.release();
        await expect(page.locator("[data-card-outline-section=description]")).toContainText("Delayed history description");
        await expect(page.locator("[data-card-outline-section=description]")).toBeFocused();
        expect(fixture.reads.filter((path) => path.endsWith("/comments"))).toHaveLength(1);
        expect(timelineReads).toHaveLength(2);
        if (width === 1440) await expect(page.getByRole("separator", { name: "Comments panel width" })).toBeVisible();
    });

test("unread navigation waits for its history section and preserves native highlighting", async ({ page }) => {
    const fixture = await prepareDelayedLens(page, { unread: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
    fixture.release();
    await expect(page.locator("[data-card-outline-section=description]")).toBeFocused();
    await expect(page.locator("[data-card-outline-section=description]")).toHaveClass(/unread-target-highlight/);
    expect(fixture.reads.filter((path) => path.endsWith("/seen"))).toHaveLength(1);
});

test("editing opens the same card history and retains title drafts through discard and flip gating", async ({ page }) => {
    const fixture = await prepareDelayedLens(page, { editable: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
    fixture.release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await page.getByRole("heading", { name: "Lens card", exact: true }).click();
    const title = page.locator("textarea").filter({ visible: true }).first();
    await expect(title).toBeVisible();
    await title.fill("Retained title draft");
    await expect(title).toHaveValue("Retained title draft");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Lens card", exact: true })).toBeVisible();
    expect(fixture.writes).toEqual([]);
    await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Flip card", exact: true }).click();
    await expect(page.getByTestId("flipped-card")).toHaveText("fixture-card");
});

for (const failSave of [false, true])
    test(`native card save ${failSave ? "retains the failed draft" : "submits the edited title"}`, async ({ page }) => {
        const fixture = await prepareDelayedLens(page, { editable: true, failSave });
        await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
        fixture.release();
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
        await page.getByRole("heading", { name: "Lens card", exact: true }).click();
        const title = page.locator("textarea").filter({ visible: true }).first();
        await expect(title).toBeEnabled();
        await title.fill("Saved title draft");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => fixture.writes.length).toBe(1);
        expect(fixture.writes[0]).toMatchObject({ title: "Saved title draft" });
        if (failSave) {
            await expect(title).toHaveValue("Saved title draft");
            await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
            await page.getByRole("button", { name: "Cancel", exact: true }).click();
        } else await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeEnabled();
    });

test("card and account switches close context without displaying the previous history", async ({ page }) => {
    await prepareDelayedLens(page);
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await expect(page.getByRole("region", { name: "Lens card", exact: true })).toContainText("Delayed history description");
    await page.getByRole("button", { name: "Actions", exact: true }).click();
    await page.getByRole("button", { name: "Review", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Review", exact: true })).toBeVisible();
    await page
        .getByRole("button", { name: "Switch card", exact: true, includeHidden: true })
        .evaluate((button) => (button as HTMLButtonElement).click());
    await expect(page.getByRole("dialog", { name: "Review", exact: true })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Other lens card", exact: true })).not.toContainText("Delayed history description");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByRole("tablist")).toHaveCount(0);
});

test("unknown verification and agent lifecycle never invent approval actions", async ({ page }) => {
    await prepareDelayedLens(page, { unknown: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await page.getByRole("button", { name: "Actions", exact: true }).click();
    await page.getByRole("button", { name: "Execution", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Execution", exact: true })).toContainText("Agent lifecycle: Unknown");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Review", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Review", exact: true })).toContainText("Verification: Unknown");
    await expect(page.getByRole("button", { name: /Approve/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
});

test("deadline draft removal is discarded through the original card editor", async ({ page }) => {
    const fixture = await prepareDelayedLens(page, { editable: true, deadline: true });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("region", { name: "Lens card", exact: true })).toBeVisible();
    fixture.release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await page.getByText(/2030/).first().click();
    await page.getByRole("button", { name: "Remove deadline", exact: true }).click();
    await expect(page.getByRole("button", { name: "Remove deadline", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByText(/2030/).first()).toBeVisible();
    expect(fixture.writes).toEqual([]);
});

test("one comment composer retains its draft while the card changes available width", async ({ page }) => {
    const fixture = await prepareDelayedLens(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    fixture.release();
    const prompt = page.getByRole("button", { name: /Add a comment as/ });
    await expect(prompt).toHaveCount(1);
    await prompt.click();
    const editor = page.locator("[data-card-comment-form] [data-slate-editor=true]");
    await expect(editor).toHaveCount(1);
    await editor.fill("Retained narrow comment draft");
    for (const width of [720, 390, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        await expect(editor).toHaveCount(1);
        await expect(editor).toContainText("Retained narrow comment draft");
        await expect(editor).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    }
    const submitted = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname.endsWith("/comment"));
    await page.getByRole("button", { name: "Save", exact: true }).last().click();
    expect((await submitted).postDataJSON().content).toContain("Retained narrow comment draft");
});

for (const denyStorage of [false, true]) {
    test(`comment drafts remain account scoped with unavailable storage=${denyStorage}`, async ({ page }) => {
        const fixture = await prepareDelayedLens(page);
        await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
        fixture.release();
        const result = await page.evaluate(async (deny) => {
            const { getCardCommentDraftStore } = await import(String("/src/core/stores/CardCommentDraftStore.ts"));
            const { default: auth } = await import(String("/src/core/stores/AuthStore.ts"));
            const first = auth.getState().currentUser!;
            if (deny) {
                for (const method of ["getItem", "setItem", "removeItem"] as const)
                    Storage.prototype[method] = () => {
                        throw new DOMException("Denied", "SecurityError");
                    };
            }
            const store = getCardCommentDraftStore();
            store.saveDraft(first.uid, "fixture", "fixture-card", "Private first-account draft");
            auth.setState({ currentUser: { ...first, uid: "second-comment-user" } });
            const second = store.getDraft("second-comment-user", "fixture", "fixture-card");
            store.saveDraft("second-comment-user", "fixture", "fixture-card", "Second-account draft");
            auth.setState({ currentUser: first });
            const restored = store.getDraft(first.uid, "fixture", "fixture-card");
            store.clearDraft(first.uid, "fixture", "fixture-card");
            return { second, restored, cleared: store.getDraft(first.uid, "fixture", "fixture-card") };
        }, denyStorage);
        expect(result).toEqual({ second: "", restored: "Private first-account draft", cleared: "" });
    });
}

test("switching accounts while composing cannot save the previous account draft into the next account", async ({ page }) => {
    const fixture = await prepareDelayedLens(page);
    await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1");
    fixture.release();
    const prompt = page.getByRole("button", { name: /Add a comment as/ });
    await prompt.click();
    const editor = page.locator("[data-card-comment-form] [data-slate-editor=true]");
    await editor.fill("Private retained first account comment");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await prompt.click();
    await expect(editor).toHaveCount(1);
    await expect(editor).not.toContainText("Private retained first account comment");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await prompt.click();
    await expect(editor).toContainText("Private retained first account comment");
});

for (const width of [1440, 320, 390]) {
    test(`one relationship entry retains both directions and selection at ${width}px`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.setViewportSize({ width, height: 844 });
        const edges = [
            { uid: "edge-parent", parent_card_uid: "parent-card", child_card_uid: "fixture-card", relationship_type_uid: "relationship-type" },
            { uid: "edge-child", parent_card_uid: "fixture-card", child_card_uid: "child-card", relationship_type_uid: "relationship-type" },
        ];
        const updates: { is_parent: boolean; relationships: string[][] }[] = [];
        await page.route(/\/board\/fixture\/card\/fixture-card\?sections=/, (route) => {
            const sections = new URL(route.request().url()).searchParams.get("sections")!.split(",");
            const result: Record<string, unknown> = {
                card: { ...lensCard(), relationships: edges, description: { content: "Relationship source body" } },
                whispers_allowed: false,
            };
            for (const section of sections) if (section !== "description") result[section] = [];
            return route.fulfill({ json: result });
        });
        await page.route("**/board/fixture/card/fixture-card/relationships", (route) => {
            updates.push(route.request().postDataJSON());
            return route.fulfill({ json: { relationships: edges } });
        });
        await page.route("**/board/fixture/card/*/seen", (route) => route.fulfill({ json: { card_uid: "fixture-card", seen_change_seq: 0 } }));
        await page.route(/^http:\/\/localhost:5381\/activity\//, (route) => route.fulfill({ json: { records: [], next_cursor: null } }));
        await page.goto("/src/components/Layout/workbench-shell.fixture.html?lenses=1&relations=1&lang=en-US");
        await expect(page.getByRole("region", { name: "Lens card", exact: true })).toContainText("Relationship source body");
        if (width < 640) await page.getByRole("button", { name: "Actions", exact: true }).click();
        const trigger = page.getByRole("button", { name: "Relationships", exact: true });
        await expect(trigger).toHaveCount(1);
        await trigger.focus();
        await trigger.press("Enter");
        const parent = page.getByRole("region", { name: "Parent cards", exact: true });
        const child = page.getByRole("region", { name: "Child cards", exact: true });
        await expect(parent).toContainText("parent-card");
        await expect(parent).not.toContainText("child-card");
        await expect(child).toContainText("child-card");
        await expect(child).not.toContainText("parent-card");
        await parent.getByRole("button", { name: "Select cards", exact: true }).click();
        await expect.poll(async () => JSON.parse((await page.getByTestId("relationship-selection").textContent())!).type).toBe("parents");
        await page.getByRole("button", { name: "Cancel relationship selection", exact: true }).click();
        await expect(parent).toBeVisible();
        await parent.getByRole("button", { name: "Select cards", exact: true }).click();
        await page.getByRole("button", { name: "Save relationship selection", exact: true }).click();
        await expect.poll(() => updates).toEqual([{ is_parent: true, relationships: [["parent-card", "relationship-type"]] }]);
        await expect(parent).toBeVisible();
        await child.getByRole("button", { name: "Select cards", exact: true }).click();
        await expect.poll(async () => JSON.parse((await page.getByTestId("relationship-selection").textContent())!).type).toBe("children");
        await page.getByRole("button", { name: "Save relationship selection", exact: true }).click();
        await expect
            .poll(() => updates)
            .toEqual([
                { is_parent: true, relationships: [["parent-card", "relationship-type"]] },
                { is_parent: false, relationships: [["child-card", "relationship-type"]] },
            ]);
        await expect(child).toBeVisible();
        const relationPanel = page.getByRole("dialog").filter({ has: child });
        await expect(relationPanel).toHaveAttribute("data-state", "open");
        const close = relationPanel.getByRole("button", { name: "Close", exact: true });
        await expect(close).toBeEnabled();
        await close.focus();
        await expect(page.getByRole("tooltip")).toHaveCount(0);
        await close.press("Escape");
        await expect(child).toHaveCount(0);
        await expect(trigger).toBeFocused();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(errors).toEqual([]);
    });
}
