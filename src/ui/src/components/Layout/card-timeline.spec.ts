import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
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
        expect(cursors.filter((cursor) => cursor !== null)).toEqual(["activity:move"]);
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
}
