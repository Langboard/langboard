import { expect, test } from "@playwright/test";

const compactTime = "2026-10-01T12:00:00Z";

test("comment timeline shows a successful create without a socket event or page reload", async ({ page }) => {
    let committed = false;
    let reads = 0;
    const comment = originalEvidence("created", "Created without socket").data;
    await page.route(/\/board\/timeline-project\/card\/first-card\/comments?(?:\?|$)/, async (route) => {
        if (route.request().method() === "POST") {
            committed = true;
            return route.fulfill({ status: 201, json: {} });
        }
        return route.fulfill({ json: { comments: committed ? [comment] : [] } });
    });
    await page.route(/\/timeline(?:\?|$)/, async (route) => {
        reads += 1;
        return route.fulfill({ json: { records: committed ? [originalEvidence("created", "Created without socket")] : [], next_cursor: null } });
    });
    await page.goto("/src/components/Layout/card-timeline.fixture.html?commentCreate");
    await expect.poll(() => reads).toBeGreaterThan(0);
    const initialReads = reads;
    await page.getByRole("button", { name: "Create comment without socket", exact: true }).click();
    await expect(page.getByRole("region", { name: "Comment create readback" }).getByText("Created without socket", { exact: true })).toBeVisible();
    expect(reads).toBeGreaterThan(initialReads);
});
const compactKey = "a".repeat(64);
const compactEligibility = { can_generate: true, provider_configured: true, needs_compaction: true, next_request_key: compactKey };
const originalEvidence = (uid: string, text: string) => ({
    kind: "comment",
    uid,
    evidence_ref: `comment:${uid}`,
    created_at: compactTime,
    data: {
        uid,
        card_uid: "first-card",
        created_at: compactTime,
        updated_at: compactTime,
        content: { content: text },
        is_edited: false,
        reactions: {},
        user: { uid: "timeline-user", type: "user", firstname: "Timeline", lastname: "Reader", created_at: compactTime, updated_at: compactTime },
    },
});
const compactBlock = {
    generated_by: { uid: "compactor-profile", display_name: "Compactor <img src=x onerror=alert(1)>" },
    uid: "summary-one",
    status: "completed",
    request_key: compactKey,
    source_revision: "source-one",
    covered_from: compactTime,
    covered_through: compactTime,
    evidence_refs: ["comment:evidence-first", "comment:evidence-last"],
    draft: {
        claims: [{ kind: "reported_failure", text: "Reported failure <img src=x onerror=alert(1)>", evidence_refs: ["comment:evidence-last"] }],
    },
};
const compactEntry = {
    kind: "compaction",
    uid: compactBlock.uid,
    evidence_ref: `compaction:${compactBlock.uid}`,
    created_at: compactTime,
    data: compactBlock,
};

for (const width of [1440, 390]) {
    test(`comment timeline defers socket refresh until older history finishes at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let releaseOlder!: () => void;
        const olderGate = new Promise<void>((resolve) => (releaseOlder = resolve));
        let olderStarted!: () => void;
        const olderRequest = new Promise<void>((resolve) => (olderStarted = resolve));
        let firstPageRequests = 0;
        await page.route(/\/timeline(?:\?|$)/, async (route) => {
            const before = new URL(route.request().url()).searchParams.get("before");
            if (before) {
                olderStarted();
                await olderGate;
            } else firstPageRequests++;
            await route.fulfill({
                json: {
                    records: [originalEvidence(before ? "older" : "newest", before ? "Older source" : "Newest source")],
                    next_cursor: before ? null : "comment:newest",
                },
            });
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await expect(page.getByText("Newest source", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Older history", exact: true }).click();
        await olderRequest;
        const beforeRefresh = firstPageRequests;
        await page.getByRole("button", { name: "Socket reopened", exact: true }).click();
        try {
            // The existing socket debounce is 100ms; keep pagination in flight past it.
            await page.waitForTimeout(300);
            expect(firstPageRequests).toBe(beforeRefresh);
        } finally {
            releaseOlder();
        }
        await expect(page.getByText("Older source", { exact: true })).toBeVisible();
        await expect.poll(() => firstPageRequests).toBeGreaterThan(beforeRefresh);
        await expect(page.locator("[data-card-comment-uid]")).toHaveCount(2);
    });

    test(`comment timeline older history remains available during background refresh at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let holdRefresh = false;
        let releaseRefresh!: () => void;
        const refreshGate = new Promise<void>((resolve) => (releaseRefresh = resolve));
        let refreshStarted!: () => void;
        const refreshRequest = new Promise<void>((resolve) => (refreshStarted = resolve));
        await page.route(/\/timeline(?:\?|$)/, async (route) => {
            const before = new URL(route.request().url()).searchParams.get("before");
            if (!before && holdRefresh) {
                refreshStarted();
                await refreshGate;
            }
            await route.fulfill({
                json: {
                    records: [
                        originalEvidence(before ? "older" : "newest", before ? "Older source" : holdRefresh ? "Refreshed source" : "Newest source"),
                    ],
                    next_cursor: before ? null : "comment:newest",
                },
            });
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await expect(page.getByText("Newest source", { exact: true })).toBeVisible();
        holdRefresh = true;
        await page.getByRole("button", { name: "Socket reopened", exact: true }).click();
        await refreshRequest;
        try {
            const older = page.getByRole("button", { name: "Older history", exact: true });
            await expect(older).toBeEnabled();
            await older.click();
            await expect(page.getByText("Older source", { exact: true })).toBeVisible();
            await expect(page.locator("[data-card-comment-uid]")).toHaveCount(2);
        } finally {
            releaseRefresh();
        }
        await expect(page.getByText("Refreshed source", { exact: true })).toBeVisible();
        await expect(page.getByText("Older source", { exact: true })).toBeVisible();
        await expect(page.locator("[data-card-comment-uid]")).toHaveCount(2);
    });

    test(`comment timeline removes cached content when current access is revoked at ${width}px`, async ({ page }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({ width, height: 900 });
        let denied = false;
        await page.route(/\/timeline(?:\?|$)/, async (route) => {
            await route.fulfill(
                denied
                    ? { status: 404, json: {} }
                    : {
                          json: {
                              records: [
                                  {
                                      kind: "activity",
                                      uid: "visible-before-revocation",
                                      evidence_ref: "activity:visible-before-revocation",
                                      created_at: "2026-10-01T12:00:00Z",
                                      data: {
                                          uid: "visible-before-revocation",
                                          activity_type: "card_moved",
                                          created_at: "2026-10-01T12:00:00Z",
                                          from_column: { name: "Private review" },
                                          column: { name: "Private doing" },
                                      },
                                  },
                              ],
                              next_cursor: null,
                          },
                      }
            );
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await expect(page.locator("[data-card-activity-uid]")).toContainText("Private review › Private doing", { timeout: 45_000 });
        denied = true;
        await page.getByRole("button", { name: "Socket reopened", exact: true }).click();
        await expect(page.locator("[data-card-activity-uid]")).toHaveCount(0);
        await expect(page.getByText("Private doing", { exact: false })).toHaveCount(0);
    });

    test(`comment timeline clears cached content on account change at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.route(/\/timeline(?:\?|$)/, (route) =>
            route.fulfill({
                json: {
                    records: [
                        {
                            kind: "activity",
                            uid: "old-account-event",
                            evidence_ref: "activity:old-account-event",
                            created_at: "2026-10-01T12:00:00Z",
                            data: {
                                uid: "old-account-event",
                                activity_type: "card_moved",
                                created_at: "2026-10-01T12:00:00Z",
                                from_column: { name: "Old account review" },
                                column: { name: "Old account doing" },
                            },
                        },
                    ],
                    next_cursor: null,
                },
            })
        );
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await expect(page.locator("[data-card-activity-uid]")).toContainText("Old account review", { timeout: 45_000 });
        await page.getByRole("button", { name: "Change timeline account", exact: true }).click();
        await expect(page.locator("[data-card-activity-uid]")).toHaveCount(0);
        await expect(page.getByText("Old account review", { exact: false })).toHaveCount(0);
    });

    test(`comment timeline interleaves persisted moves and pages without duplicate comments at ${width}px`, async ({ page }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({ width, height: 900 });
        const timestamp = "2026-10-01T12:00:00Z";
        const comment = (uid: string, text: string) => ({
            kind: "comment",
            uid,
            evidence_ref: `comment:${uid}`,
            created_at: timestamp,
            data: {
                uid,
                card_uid: "first-card",
                created_at: timestamp,
                updated_at: timestamp,
                content: { content: text },
                is_edited: false,
                reactions: {},
                user: { uid: "timeline-user", type: "user", firstname: "Timeline", lastname: "Reader", created_at: timestamp, updated_at: timestamp },
            },
        });
        const move = {
            kind: "activity",
            uid: "move",
            evidence_ref: "activity:move",
            created_at: timestamp,
            data: {
                uid: "move",
                activity_type: "card_moved",
                created_at: timestamp,
                from_column: { name: "Review" },
                column: { name: "Doing" },
                recorder: { type: "user", firstname: "Original", lastname: "Mover" },
            },
        };
        const cursors: (string | null)[] = [];
        await page.route(/\/timeline(?:\?|$)/, async (route) => {
            const url = new URL(route.request().url());
            const before = url.searchParams.get("before");
            cursors.push(before);
            await route.fulfill({
                json: url.pathname.includes("second-card")
                    ? { records: [], next_cursor: null }
                    : before
                      ? { records: [comment("older", "Older original comment"), comment("first", "Newest original comment")], next_cursor: null }
                      : { records: [comment("first", "Newest original comment"), move], next_cursor: "activity:move" },
            });
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        const first = page.locator("[data-card-comment-uid]");
        await expect(first).toHaveCount(1, { timeout: 45_000 });
        await expect(first).toContainText("Newest original comment");
        await expect(page.locator("[data-card-activity-uid]")).toHaveAttribute("aria-label", /Review › Doing.*Original Mover/);
        await expect(page.getByRole("button", { name: "Older history", exact: true })).toBeEnabled();
        await page.getByRole("button", { name: "Older history", exact: true }).click();
        await expect(first).toHaveCount(2);
        const order = await page
            .locator("[data-card-comment-uid], [data-card-activity-uid]")
            .evaluateAll((elements) =>
                elements.map((element) => element.getAttribute("data-card-comment-uid") ?? element.getAttribute("data-card-activity-uid"))
            );
        expect(order).toEqual(["first", "move", "older"]);
        expect(cursors[0]).toBeNull();
        // A queued reconnect refresh can revalidate both loaded pages.
        // Every older-page request must retain the server cursor; rendered records stay unique.
        const olderCursors = cursors.filter((cursor) => cursor !== null);
        expect(olderCursors.length).toBeGreaterThan(0);
        expect(olderCursors.every((cursor) => cursor === "activity:move")).toBe(true);
        await page.getByRole("button", { name: "Switch timeline card", exact: true }).click();
        await expect(page.getByText("No comments", { exact: true })).toBeVisible();
        await expect(first).toHaveCount(0);
        await expect(page.locator("[data-card-activity-uid]")).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

    test(`comment timeline refresh defers while editing and reconciles on reconnect at ${width}px`, async ({ page }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({ width, height: 900 });
        let requests = 0;
        let changed = false;
        await page.route(/\/timeline(?:\?|$)/, async (route) => {
            requests++;
            const timestamp = "2026-10-01T12:00:00Z";
            await route.fulfill({
                json: {
                    records: [
                        {
                            kind: "comment",
                            uid: "editing",
                            evidence_ref: "comment:editing",
                            created_at: timestamp,
                            data: {
                                uid: "editing",
                                card_uid: "first-card",
                                created_at: timestamp,
                                updated_at: timestamp,
                                content: { content: changed ? "Fresh server comment" : "Original editable comment" },
                                is_edited: false,
                                reactions: {},
                                user: {
                                    uid: "timeline-user",
                                    type: "user",
                                    firstname: "Timeline",
                                    lastname: "Reader",
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
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        const comment = page.locator("[data-card-comment-uid]");
        await expect(comment).toContainText("Original editable comment", { timeout: 45_000 });
        await comment.getByRole("button", { name: "Edit", exact: true }).click();
        const editor = comment.locator("[contenteditable=true]");
        await expect(editor).toBeVisible({ timeout: 15_000 });
        await editor.click();
        await page.keyboard.press("End");
        await page.keyboard.type(" unsaved draft");
        const beforeRefresh = requests;
        changed = true;
        await page.getByRole("button", { name: "Rotate same user token", exact: true }).click();
        await page.waitForTimeout(300);
        await expect(editor).toContainText("unsaved draft");
        expect(requests).toBe(beforeRefresh);
        await page.getByRole("button", { name: "Committed activity", exact: true }).click();
        await page.waitForTimeout(300);
        expect(requests).toBe(beforeRefresh);
        await expect(editor).toContainText("unsaved draft");
        await comment.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(comment).toContainText("Fresh server comment");
        expect(requests).toBe(beforeRefresh + 1);
        await page.getByRole("button", { name: "Socket reopened", exact: true }).click();
        await expect.poll(() => requests).toBe(beforeRefresh + 2);
        await expect(comment).toHaveCount(1);
    });
    test(`comment timeline compact block loads cited originals only on request at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const evidenceCursors: (string | null)[] = [];
        await page.route(/\/timeline(?:\?|$)/, async (route) => {
            expect(new URL(route.request().url()).searchParams.get("compact")).toBe("true");
            await route.fulfill({
                json: {
                    records: [originalEvidence("new-comment", "New comment outside summary"), compactEntry],
                    next_cursor: null,
                    eligibility: { ...compactEligibility, needs_compaction: false, next_request_key: null },
                    in_flight: [],
                },
            });
        });
        await page.route(/\/comment-compactions\/summary-one\/evidence(?:\?|$)/, async (route) => {
            const before = new URL(route.request().url()).searchParams.get("before");
            evidenceCursors.push(before);
            await route.fulfill({
                json: before
                    ? { records: [originalEvidence("evidence-last", "Cited original preserved")], next_cursor: null }
                    : { records: [originalEvidence("evidence-first", "Earlier original preserved")], next_cursor: "comment:evidence-first" },
            });
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        const block = page.locator("[data-comment-compaction-uid]");
        await expect(block).toContainText(compactBlock.draft.claims[0].text, { timeout: 45_000 });
        await expect(block.locator("[data-generated-by-uid=compactor-profile]")).toContainText(compactBlock.generated_by.display_name);
        await expect(block.locator("[data-generated-by-uid] img")).toHaveCount(0);
        await expect(page.getByText("New comment outside summary", { exact: true })).toBeVisible();
        expect(evidenceCursors).toEqual([]);
        await expect(block.locator("img")).toHaveCount(0);
        await expect(block.getByText("Reported failure", { exact: true })).toBeVisible();
        await block.getByRole("button", { name: "Evidence 2", exact: true }).click();
        await expect(block.getByText("Cited original preserved", { exact: true })).toBeVisible();
        expect(evidenceCursors).toEqual([null, "comment:evidence-first"]);
        await page.screenshot({ path: `.verification/compaction-evidence-${width}.png`, fullPage: true });
        await page.evaluate(() => document.documentElement.classList.add("dark"));
        await page.screenshot({ path: `.verification/compaction-evidence-dark-${width}.png`, fullPage: true });
        await expect(page.locator("#card-evidence-summary-one-comment\\:evidence-last")).toBeFocused();
        await block.getByRole("button", { name: "Hide original comments", exact: true }).click();
        await expect(block.getByText("Cited original preserved", { exact: true })).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

    test(`comment timeline compaction cancellation blocks late results and retry keeps request key at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const posts: { expected_request_key: string; retry: boolean }[] = [];
        let release: (() => void) | undefined;
        let complete = false;
        let cancelled = false;
        let releaseRetry: (() => void) | undefined;
        await page.route(/\/timeline(?:\?|$)/, (route) =>
            route.fulfill({
                json: {
                    records: complete ? [compactEntry] : [originalEvidence("evidence-first", "Original before compaction")],
                    next_cursor: null,
                    eligibility: { ...compactEligibility, needs_compaction: !complete, next_request_key: complete ? null : compactKey },
                    in_flight: [],
                },
            })
        );
        await page.route(/\/comment-compactions(?:\/summary-one(?:\/cancel)?)?(?:\?|$)/, async (route) => {
            const url = new URL(route.request().url());
            if (url.pathname.endsWith("/cancel")) {
                cancelled = true;
                await route.fulfill({ json: { uid: "summary-one", status: "cancelled", request_key: compactKey } });
                release?.();
            } else if (route.request().method() === "POST") {
                posts.push(route.request().postDataJSON());
                if (posts.length === 1) {
                    await new Promise<void>((resolve) => {
                        release = resolve;
                    });
                    await route.fulfill({ json: compactBlock }).catch(() => {});
                } else {
                    cancelled = false;
                    await new Promise<void>((resolve) => {
                        releaseRetry = resolve;
                    });
                    complete = true;
                    await route.fulfill({ json: compactBlock });
                }
            } else {
                const running = { uid: "summary-one", status: cancelled ? "cancelled" : "running", request_key: compactKey };
                await route.fulfill({ json: url.pathname.endsWith("/summary-one") ? running : { records: [running] } });
            }
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await page.getByRole("button", { name: "Compact comments", exact: true }).click();
        await expect(page.getByRole("button", { name: "Cancel compaction", exact: true })).toBeEnabled();
        await page.getByRole("button", { name: "Cancel compaction", exact: true }).click();
        await expect(page.getByText("Compaction cancelled.", { exact: true })).toBeVisible();
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(0);
        await page.getByRole("button", { name: "Retry compaction", exact: true }).click();
        await expect.poll(() => posts.length).toBe(2);
        await expect(page.getByText("Compacting comments", { exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Retry compaction", exact: true })).toHaveCount(0);
        releaseRetry?.();
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(1);
        expect(posts).toEqual([
            { expected_request_key: compactKey, retry: false },
            { expected_request_key: compactKey, retry: true },
        ]);
    });

    test(`comment timeline invalidated evidence restores originals and read role cannot generate at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let invalidated = false;
        await page.route(/\/timeline(?:\?|$)/, (route) =>
            route.fulfill({
                json: {
                    records: invalidated ? [originalEvidence("evidence-last", "Changed original remains readable")] : [compactEntry],
                    next_cursor: null,
                    eligibility: { ...compactEligibility, can_generate: false },
                    in_flight: [],
                },
            })
        );
        await page.route(/\/comment-compactions\/summary-one\/evidence(?:\?|$)/, async (route) => {
            invalidated = true;
            await route.fulfill({ status: 409, json: { code: "compaction_invalidated" } });
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(1);
        await expect(page.getByRole("button", { name: "Compact comments", exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "Show original comments", exact: true }).click();
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(0);
        await expect(page.getByText("Changed original remains readable", { exact: true })).toBeVisible();
    });
    test(`comment timeline compaction late response cannot populate another card at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let release: (() => void) | undefined;
        await page.route(/\/timeline(?:\?|$)/, (route) =>
            route.fulfill({
                json: {
                    records: [],
                    next_cursor: null,
                    eligibility: route.request().url().includes("first-card") ? compactEligibility : undefined,
                },
            })
        );
        await page.route(/\/comment-compactions(?:\?|$)/, async (route) => {
            if (route.request().method() === "POST") {
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
                await route.fulfill({ json: compactBlock }).catch(() => {});
            } else await route.fulfill({ json: { records: [] } });
        });
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await page.getByRole("button", { name: "Compact comments", exact: true }).click();
        await expect.poll(() => !!release).toBe(true);
        await page.getByRole("button", { name: "Switch timeline card", exact: true }).click();
        release?.();
        await expect(page.getByText("No comments", { exact: true })).toBeVisible();
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Cancel compaction", exact: true })).toHaveCount(0);
    });

    test(`comment timeline evidence permission loss hides cached summary and comments at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.route(/\/timeline(?:\?|$)/, (route) =>
            route.fulfill({
                json: {
                    records: [originalEvidence("new-comment", "Private outside summary"), compactEntry],
                    next_cursor: null,
                    eligibility: { ...compactEligibility, needs_compaction: false },
                },
            })
        );
        await page.route(/\/comment-compactions\/summary-one\/evidence(?:\?|$)/, (route) => route.fulfill({ status: 404, json: {} }));
        await page.goto("/src/components/Layout/card-timeline.fixture.html");
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(1);
        await page.getByRole("button", { name: "Show original comments", exact: true }).click();
        await expect(page.locator("[data-comment-compaction-uid]")).toHaveCount(0);
        await expect(page.getByText("Private outside summary", { exact: true })).toHaveCount(0);
    });
}

const historyEvent = (uid: string, name = uid) => ({
    uid,
    activity_type: "card_moved",
    created_at: "2026-10-01T12:00:00Z",
    from_column: { name: "Original review" },
    column: { name },
    recorder: { type: "user", firstname: "Original", lastname: "Mover" },
});
const historyURL = "/src/components/Layout/card-timeline.fixture.html?columnHistory";

for (const width of [1440, 390]) {
    test(`column history removes cached records on read revocation and rereads latest after restoration at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const cursors: (string | null)[] = [];
        await page.route(/\/column-history(?:\?|$)/, async (route) => {
            const before = new URL(route.request().url()).searchParams.get("before_activity_uid");
            cursors.push(before);
            await route.fulfill({
                json: before
                    ? { records: [historyEvent("older-private")], next_cursor: null }
                    : { records: [historyEvent("latest-private")], next_cursor: "latest-private" },
            });
        });
        await page.goto(historyURL);
        const events = page.locator("[data-card-activity-uid]");
        await expect(events).toHaveAttribute("data-card-activity-uid", "latest-private", { timeout: 45_000 });
        await page.getByRole("button", { name: "Older history", exact: true }).click();
        await expect(events).toHaveAttribute("data-card-activity-uid", "older-private");
        await page.getByRole("button", { name: "Revoke history read", exact: true }).click();
        await expect(events).toHaveCount(0);
        await expect(page.getByRole("navigation", { name: "Activity", exact: true })).toHaveCount(0);
        const beforeActivity = cursors.length;
        await page.getByRole("button", { name: "Committed activity", exact: true }).click();
        await page.getByRole("button", { name: "Socket reopened", exact: true }).click();
        await page.waitForTimeout(200);
        expect(cursors).toHaveLength(beforeActivity);
        await page.getByRole("button", { name: "Restore history read", exact: true }).click();
        await expect(events).toHaveAttribute("data-card-activity-uid", "latest-private");
        expect(cursors.at(-1)).toBeNull();
        expect(cursors).toEqual([null, "latest-private", null]);
    });

    test(`column history rejects a delayed response after read permission is removed at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let release: (() => void) | undefined;
        let fulfilled = false;
        await page.route(/\/column-history(?:\?|$)/, async (route) => {
            await new Promise<void>((resolve) => {
                release = resolve;
            });
            await route.fulfill({ json: { records: [historyEvent("late-private")], next_cursor: null } }).catch(() => {});
            fulfilled = true;
        });
        await page.goto(historyURL);
        await expect.poll(() => !!release).toBe(true);
        await page.getByRole("button", { name: "Revoke history read", exact: true }).click();
        release?.();
        await expect.poll(() => fulfilled).toBe(true);
        await page.waitForTimeout(100);
        await expect(page.locator("[data-card-activity-uid]")).toHaveCount(0);
        await expect(page.getByRole("navigation", { name: "Activity", exact: true })).toHaveCount(0);
    });

    for (const boundary of ["card", "project"] as const) {
        test(`column history rejects a delayed response from another ${boundary} at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            let release: (() => void) | undefined;
            let fulfilled = false;
            const requests: string[] = [];
            await page.route(/\/column-history(?:\?|$)/, async (route) => {
                const url = route.request().url();
                requests.push(url);
                if (requests.length === 1) {
                    await new Promise<void>((resolve) => {
                        release = resolve;
                    });
                    await route.fulfill({ json: { records: [historyEvent("wrong-scope")], next_cursor: null } }).catch(() => {});
                    fulfilled = true;
                } else await route.fulfill({ json: { records: [historyEvent("current-scope")], next_cursor: null } });
            });
            await page.goto(historyURL);
            await expect.poll(() => !!release).toBe(true);
            await page.getByRole("button", { name: boundary === "card" ? "Switch timeline card" : "Switch history project", exact: true }).click();
            await expect(page.locator("[data-card-activity-uid]")).toHaveAttribute("data-card-activity-uid", "current-scope");
            release?.();
            await expect.poll(() => fulfilled).toBe(true);
            await page.waitForTimeout(100);
            await expect(page.locator("[data-card-activity-uid]")).toHaveCount(1);
            await expect(page.locator("[data-card-activity-uid]")).toHaveAttribute("data-card-activity-uid", "current-scope");
            expect(requests[1]).toContain(boundary === "card" ? "/second-card/column-history" : "/other-project/card/");
        });
    }

    test(`column history refreshes the current session after a delayed old credential response at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let release: (() => void) | undefined;
        let fulfilled = false;
        let requests = 0;
        await page.route(/\/column-history(?:\?|$)/, async (route) => {
            requests += 1;
            if (requests === 1) {
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
                await route.fulfill({ json: { records: [historyEvent("old-session")], next_cursor: null } }).catch(() => {});
                fulfilled = true;
            } else await route.fulfill({ json: { records: [historyEvent("current-session")], next_cursor: null } });
        });
        await page.goto(historyURL);
        await expect.poll(() => !!release).toBe(true);
        await page.getByRole("button", { name: "Rotate same user token", exact: true }).click();
        release?.();
        await expect.poll(() => fulfilled).toBe(true);
        await expect(page.locator("[data-card-activity-uid]")).toHaveAttribute("data-card-activity-uid", "current-session");
        expect(requests).toBe(2);
    });

    for (const boundary of ["account", "logout"] as const) {
        test(`column history rejects a delayed response after ${boundary} at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            let release: (() => void) | undefined;
            let fulfilled = false;
            let requests = 0;
            await page.route(/\/column-history(?:\?|$)/, async (route) => {
                requests += 1;
                if (requests === 1) {
                    await new Promise<void>((resolve) => {
                        release = resolve;
                    });
                    await route.fulfill({ json: { records: [historyEvent("previous-identity")], next_cursor: null } }).catch(() => {});
                    fulfilled = true;
                } else await route.fulfill({ json: { records: [], next_cursor: null } });
            });
            await page.goto(historyURL);
            await expect.poll(() => !!release).toBe(true);
            await page
                .getByRole("button", {
                    name: boundary === "account" ? "Change timeline account" : "Sign out history user",
                    exact: true,
                })
                .click();
            release?.();
            await expect.poll(() => fulfilled).toBe(true);
            await page.waitForTimeout(100);
            await expect(page.locator("[data-card-activity-uid]")).toHaveCount(0);
            await expect(page.getByText("previous-identity", { exact: false })).toHaveCount(0);
        });
    }

    test(`column history preserves server cursors and source snapshots during realtime paging at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const cursors: (string | null)[] = [];
        let release: (() => void) | undefined;
        let realtime = false;
        let fulfilled = false;
        await page.route(/\/column-history(?:\?|$)/, async (route) => {
            const before = new URL(route.request().url()).searchParams.get("before_activity_uid");
            cursors.push(before);
            if (before && !release) {
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
                await route.fulfill({ json: { records: [historyEvent("obsolete-page")], next_cursor: null } }).catch(() => {});
                fulfilled = true;
            } else {
                await route.fulfill({
                    json: before
                        ? { records: [historyEvent("creation", "Original todo"), historyEvent("older-move", "Original review")], next_cursor: null }
                        : {
                              records: [historyEvent(realtime ? "committed-move" : "latest-move", "Original doing")],
                              next_cursor: "server:latest-move",
                          },
                });
            }
        });
        await page.goto(historyURL);
        const events = page.locator("[data-card-activity-uid]");
        await expect(events).toHaveAttribute("data-card-activity-uid", "latest-move", { timeout: 45_000 });
        await page.getByRole("button", { name: "Older history", exact: true }).click();
        await expect.poll(() => !!release).toBe(true);
        realtime = true;
        await page.getByRole("button", { name: "Committed activity", exact: true }).click();
        await expect(events).toHaveCount(2);
        release?.();
        await expect.poll(() => fulfilled).toBe(true);
        await expect(events.first()).toHaveAttribute("datetime", "2026-10-01T12:00:00.000Z");
        await expect(events.first()).toHaveAttribute("aria-label", /Original review › Original todo.*Original Mover/);
        await events.first().focus();
        await expect(events.first()).toBeFocused();
        await expect(page.getByRole("tooltip")).toContainText("Original Mover");
        expect(await events.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-card-activity-uid")))).toEqual([
            "creation",
            "older-move",
        ]);
        expect(cursors).toEqual([null, "server:latest-move", "server:latest-move"]);
        await page.getByRole("button", { name: "Latest history", exact: true }).click();
        await expect(events).toHaveAttribute("data-card-activity-uid", "committed-move");
        expect(cursors.at(-1)).toBeNull();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: `${test.info().outputDir}/compact-history.png`, fullPage: true });
    });

    for (const status of [403, 404]) {
        test(`column history hides a previously readable page after ${status} at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            let denied = false;
            await page.route(/\/column-history(?:\?|$)/, (route) =>
                route.fulfill(denied ? { status, json: {} } : { json: { records: [historyEvent("private-page")], next_cursor: null } })
            );
            await page.goto(historyURL);
            await expect(page.locator("[data-card-activity-uid]")).toHaveCount(1, { timeout: 45_000 });
            denied = true;
            await page.getByRole("button", { name: "Committed activity", exact: true }).click();
            await expect(page.locator("[data-card-activity-uid]")).toHaveCount(0);
            await expect(page.getByText("private-page", { exact: false })).toHaveCount(0);
        });
    }
}
