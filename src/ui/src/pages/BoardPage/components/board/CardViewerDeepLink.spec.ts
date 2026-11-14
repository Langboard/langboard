import { test, expect, type Page, type Route } from "@playwright/test";

const FIXTURE = "/src/pages/BoardPage/components/board/CardViewerDeepLink.fixture.html";

test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => console.error("Fixture page error:", error.stack ?? error.message));
    page.on("requestfailed", (request) => console.error("Fixture request failure:", new URL(request.url()).pathname, request.failure()?.errorText));
});

const NOW_ISO = new Date().toISOString();

const FIXTURE_USER = {
    uid: "fixture-user",
    created_at: NOW_ISO,
    updated_at: NOW_ISO,
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

const FIXTURE_CARD = {
    uid: "fixturecard",
    created_at: NOW_ISO,
    updated_at: NOW_ISO,
    project_uid: "fixtureproj",
    project_column_uid: "fixture-column",
    project_column_name: "Fixture column",
    title: "Fixture card",
    description: [],
    order: 0,
    count_comment: 0,
    member_uids: [],
    current_auth_role_actions: ["*"],
    project_members: [],
    labels: [],
    relationships: [],
    has_description: false,
};

/** The API client sends credentialed cross-origin requests, so the mocked
 * responses must mirror the page origin exactly instead of using "*". */
function corsHeaders(route: Route): Record<string, string> {
    const origin = route.request().headers()["origin"] || "http://127.0.0.1:4175";
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-credentials": "true",
        "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
        "access-control-allow-headers": "authorization,content-type,content-encoding",
    };
}

async function fulfillWithCors(route: Route, payload: unknown): Promise<void> {
    if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: corsHeaders(route) });
        return;
    }
    await route.fulfill({ json: payload, headers: corsHeaders(route) });
}

interface IMockOptions {
    projectDelay?: number;
    cardDelay?: number;
}

async function mockBoardApi(page: Page, options: IMockOptions = {}): Promise<void> {
    const projectPayload = {
        project: {
            uid: "fixtureproj",
            created_at: NOW_ISO,
            updated_at: NOW_ISO,
            owner_uid: FIXTURE_USER.uid,
            title: "Fixture project",
            project_type: "default",
            archive_visible_days: 30,
            all_members: [FIXTURE_USER],
            invited_member_uids: [],
            starred: false,
            internal_bots: [],
            internal_bot_settings: {},
            current_auth_role_actions: ["*"],
            labels: [],
            description: "",
            last_viewed_at: NOW_ISO,
            view_count: 0,
        },
        project_bot_scopes: [],
        project_bot_schedules: [],
    };

    const cardPayload = {
        card: FIXTURE_CARD,
        attachments: [],
        checklists: [],
        global_relationships: [],
        project_columns: [],
        project_labels: [],
        bot_scopes: [],
        execution_receipts: [],
    };

    // Catch-all first so the specific routes registered later take precedence.
    await page.route("**/board/fixtureproj/**", (route) => fulfillWithCors(route, { comments: [], replies: [], total_count: 0 }));
    await page.route("**/board/fixtureproj/card/*", async (route) => {
        if (options.cardDelay) {
            await new Promise((resolve) => setTimeout(resolve, options.cardDelay));
        }
        const url = new URL(route.request().url());
        const uid = url.pathname.split("/").at(-1)!;
        if (url.searchParams.has("location_only")) {
            await fulfillWithCors(route, { canonical_project_uid: "fixtureproj", card_uid: uid });
            return;
        }
        await fulfillWithCors(route, {
            ...cardPayload,
            card: { ...FIXTURE_CARD, uid, title: uid === "fixturecard" ? "Fixture card" : `Card ${uid}` },
        });
    });
    await page.route("**/dashboard/cards/available", (route) => {
        const cards = route.request().method() === "OPTIONS" ? [] : route.request().postDataJSON().cards;
        return fulfillWithCors(route, { cards });
    });
    await page.route("**/board/fixtureproj/column/dock", (route) => fulfillWithCors(route, { column_uids: [] }));
    await page.route("**/board/fixtureproj/columns", (route) => fulfillWithCors(route, { columns: [] }));
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj",
        async (route) => {
            if (options.projectDelay) {
                await new Promise((resolve) => setTimeout(resolve, options.projectDelay));
            }
            await fulfillWithCors(route, projectPayload);
        }
    );
    await page.route("**/activity/project/fixtureproj/card/*/column-history", (route) => fulfillWithCors(route, { records: [], next_cursor: null }));
    await page.route("**/activity/project/fixtureproj/card/*/timeline**", (route) => fulfillWithCors(route, { records: [], next_cursor: null }));
    await page.route("**/auth/**", (route) => fulfillWithCors(route, { access_token: "fixture-token", user: FIXTURE_USER, bots: [] }));
    await page.route("**/board/fixtureproj/card/*/decisions**", (route) => fulfillWithCors(route, { items: [], next_cursor: null }));
}

for (const width of [320, 390]) {
    test(`mobile card close stays below the actual workspace header at ${width}px`, async ({ page }) => {
        const pageErrors: string[] = [];
        page.on("pageerror", (error) => pageErrors.push(error.stack ?? error.message));
        await page.setViewportSize({ width, height: 844 });
        await mockBoardApi(page);
        await page.route("**/notifications**", (route) => fulfillWithCors(route, { notifications: [], has_more: false, unread_count: 0 }));
        await page.goto(`${FIXTURE}?workspaceHeader`);
        const heading = page.getByRole("heading", { name: "Fixture card", exact: true });
        await expect(heading).toBeVisible({ timeout: 45_000 });
        const close = page.getByRole("dialog").getByRole("button", { name: "Close", exact: true });
        const header = page.locator("header[data-workbench-context]");
        const closeBounds = await close.boundingBox();
        const headerBounds = await header.boundingBox();
        expect(closeBounds!.y).toBeGreaterThanOrEqual(headerBounds!.y + headerBounds!.height);
        expect(
            await close.evaluate((button) => {
                const rect = button.getBoundingClientRect();
                return button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
            })
        ).toBe(true);
        await close.click();
        await expect(page.locator("[data-card-viewer]")).toHaveCount(0);
        await expect(header).toBeVisible();
        expect(pageErrors).toEqual([]);
    });

    test(`mobile card actions stay above the actual workspace rail at ${width}px`, async ({ page }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({ width, height: 844 });
        await mockBoardApi(page);
        await page.goto(`${FIXTURE}?workspaceRail`);
        await expect(page.getByRole("heading", { name: "Fixture card", exact: true })).toBeVisible({ timeout: 45_000 });
        const actions = page.getByRole("button", { name: "Actions", exact: true });
        const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
        const actionBounds = await actions.boundingBox();
        const railBounds = await rail.boundingBox();
        expect(actionBounds!.y + actionBounds!.height).toBeLessThanOrEqual(railBounds!.y);
        await actions.click();
        await expect(page.getByRole("button", { name: "History", exact: true })).toBeVisible();
        await rail.getByRole("button", { name: "Explorer", exact: true }).click({ trial: true });
    });
}

async function collectResults(page: Page): Promise<{ viewerMounted: number; openAnimationStarts: number; events: { type: string; t: number }[] }> {
    return page.evaluate(() => {
        const globals = window as unknown as Record<string, unknown>;
        return {
            viewerMounted: (globals.__viewerMounted as number) ?? 0,
            openAnimationStarts: (globals.__openAnimationStarts as number) ?? 0,
            events: (globals.__events as { type: string; t: number }[]) ?? [],
        };
    });
}

test("deep link with a restored session plays the open animation exactly once", async ({ page }) => {
    await mockBoardApi(page, { projectDelay: 150, cardDelay: 400 });

    await page.goto(`${FIXTURE}?authDelay=0&projectDelay=150`);

    const viewer = page.locator("[data-card-viewer]");
    await expect(viewer).toBeVisible();
    // Wait for the loaded card content and let any second play happen.
    await expect(page.getByText("Fixture card").first()).toBeVisible();
    await page.waitForTimeout(900);

    const results = await collectResults(page);
    expect(results.viewerMounted).toBe(1);
    expect(results.openAnimationStarts).toBe(1);
});

test("deep link stays animated once when the app-root Suspense swaps after mount", async ({ page }) => {
    await mockBoardApi(page, { projectDelay: 150, cardDelay: 400 });

    // A late lazy chunk suspends the app-root Suspense boundary 800ms after
    // the viewer mounted; React 18 hides the subtree and restarts CSS
    // animations when it is restored. Before the mount-once gate this
    // replayed the card open animation a second time.
    await page.goto(`${FIXTURE}?authDelay=0&projectDelay=150&lateSuspense=1&lateSuspenseAt=800`);

    const viewer = page.locator("[data-card-viewer]");
    await expect(viewer).toBeVisible();
    await expect(page.getByText("Fixture card").first()).toBeVisible();
    // Cover the late suspense suspension (800ms + 500ms chunk delay) and restore.
    await page.waitForTimeout(2200);

    const results = await collectResults(page);
    expect(results.openAnimationStarts).toBe(1);
});

test("deep link under StrictMode double mounts plays the open animation exactly once", async ({ page }) => {
    await mockBoardApi(page, { projectDelay: 150, cardDelay: 400 });

    await page.goto(`${FIXTURE}?authDelay=0&projectDelay=150&strictMode=1`);

    const viewer = page.locator("[data-card-viewer]");
    await expect(viewer).toBeVisible();
    await expect(page.getByText("Fixture card").first()).toBeVisible();
    await page.waitForTimeout(900);

    const results = await collectResults(page);
    expect(results.openAnimationStarts).toBe(1);
});

test("visual blank area dismisses the card while surface and floating actions stay open", async ({ page }) => {
    await mockBoardApi(page);
    await page.goto(FIXTURE);
    const viewer = page.locator("[data-card-viewer]");
    await expect(page.locator("[data-card-surface]")).toBeVisible();
    await page.locator("[data-card-surface]").click({ position: { x: 10, y: 10 } });
    await expect(viewer).toBeVisible();
    await page.getByRole("button", { name: "Actions", exact: true }).click();
    await expect(viewer).toBeVisible();
    const surface = await page.locator("[data-card-surface]").boundingBox();
    const nav = await page.locator("[data-floating-nav-content]").locator("button").first().boundingBox();
    expect(surface).not.toBeNull();
    expect(nav).not.toBeNull();
    const background = { x: surface!.x + 8, y: (surface!.y + surface!.height + nav!.y) / 2 };
    await page.mouse.move(background.x, background.y);
    await page.mouse.down();
    await page.mouse.move(background.x + 12, background.y, { steps: 3 });
    await page.mouse.move(background.x, background.y);
    await page.mouse.up();
    await expect(viewer).toBeVisible();
    await page.mouse.click(background.x, background.y);
    await expect(viewer).toHaveCount(0);
});

async function seedTray(page: Page, count: number) {
    await page.addInitScript((count) => {
        sessionStorage.setItem(
            "langboard-card-flip-session",
            JSON.stringify({
                state: {
                    trays: {
                        "fixture-user:fixtureproj": Array.from({ length: count }, (_, index) => ({
                            uid: `other${String(index).padStart(6, "0")}`,
                            title: `Card other${String(index).padStart(6, "0")}`,
                        })),
                        "fixture-user:another-project": [{ uid: "foreign", title: "Foreign card" }],
                    },
                },
                version: 0,
            })
        );
    }, count);
}

test("Flip preserves a card, restores it and swaps a second card without mounting two viewers", async ({ page }) => {
    await mockBoardApi(page);
    await seedTray(page, 1);
    await page.goto(FIXTURE);
    await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Flip card", exact: true }).click();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(0);
    const saved = await page.evaluate(() => JSON.parse(sessionStorage.getItem("langboard-card-flip-session")!).state.trays);
    expect(saved["fixture-user:fixtureproj"].map((card: { uid: string }) => card.uid)).toEqual(["fixturecard", "other000000"]);
    await page.getByRole("button", { name: "Restore Fixture card", exact: true }).click();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
    const otherCard = page.getByRole("button", { name: "Restore Card other000000", exact: true });
    if (!(await otherCard.isVisible())) await page.getByRole("button", { name: "Flipped cards · 1", exact: true }).click();
    await otherCard.click();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
    await expect(page.getByText("Card other000000", { exact: true }).first()).toBeVisible();
    const swappedCard = page.getByRole("button", { name: "Restore Fixture card", exact: true });
    if (!(await swappedCard.isVisible())) await page.getByRole("button", { name: "Flipped cards · 1", exact: true }).click();
    await expect(swappedCard).toBeVisible();
});

test("card edit mode permits draft-preserving Flip while protecting tray swaps", async ({ page }) => {
    await mockBoardApi(page);
    await seedTray(page, 1);
    await page.goto(FIXTURE);
    await page.evaluate(() => {
        const events: string[] = [];
        (window as unknown as { __editEvents: string[] }).__editEvents = events;
        for (const name of ["pointerdown", "click", "focusin", "focusout"]) {
            document.addEventListener(
                name,
                (event) => {
                    const target = event.target as HTMLElement;
                    const label = target.getAttribute("aria-label") ?? target.textContent?.slice(0, 40);
                    events.push(`${name}:${target.tagName}:${label}:${!!target.closest("[data-dialog-content]")}`);
                },
                true
            );
        }
    });
    await page.locator("[data-floating-nav-content]").getByRole("button", { name: "Edit", exact: true }).click();
    try {
        await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeEnabled();
    } catch (error) {
        console.error(
            "Edit transition diagnostics:",
            await page.evaluate(() => ({
                events: (window as unknown as { __editEvents: string[] }).__editEvents,
                buttons: Array.from(document.querySelectorAll("[data-floating-nav-content] button")).map((button) => ({
                    text: button.textContent,
                    disabled: (button as HTMLButtonElement).disabled,
                })),
            }))
        );
        throw error;
    }
    await expect(page.getByRole("button", { name: "Restore Card other000000", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeEnabled();
});

for (const width of [360, 390, 768, 1440]) {
    for (const count of [1, 3, 10]) {
        test(`Flip tray contains ${count} cards without horizontal overflow at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 844 });
            await mockBoardApi(page);
            await seedTray(page, count);
            await page.goto(FIXTURE);
            const tray = page.locator("[data-card-flip-tray]");
            await expect(tray).toBeVisible();
            await expect(page.getByText("Foreign card", { exact: true })).toHaveCount(0);
            const bounds = await tray.boundingBox();
            expect(bounds!.x).toBeGreaterThanOrEqual(0);
            expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
            const overflow = page.getByRole("button", { name: `Flipped cards · ${count}`, exact: true });
            if (width < 768 || count === 10) {
                await overflow.click();
                await expect(
                    page.getByRole("button", { name: `Restore Card other${String(count - 1).padStart(6, "0")}`, exact: true })
                ).toBeVisible();
                await page.keyboard.press("Escape");
                await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
            }
        });
    }
}

for (const status of [403, 500]) {
    test(`tray availability ${status} ${status === 403 ? "removes inaccessible" : "preserves temporarily unavailable"} cards`, async ({ page }) => {
        await mockBoardApi(page);
        await seedTray(page, 3);
        let validated = false;
        await page.route("**/dashboard/cards/available", (route) =>
            route.request().method() === "OPTIONS"
                ? fulfillWithCors(route, {})
                : route.fulfill({ status: 404, json: {}, headers: corsHeaders(route) })
        );
        await page.route("**/board/fixtureproj/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") {
                await route.fulfill({ status: 204, headers: corsHeaders(route) });
                return;
            }
            validated = true;
            await route.fulfill({ status, json: { message: "Availability fixture" }, headers: corsHeaders(route) });
        });
        await page.goto(FIXTURE);
        await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeVisible();
        await expect.poll(() => validated).toBe(true);
        const persistedCount = () =>
            page.evaluate(() => JSON.parse(sessionStorage.getItem("langboard-card-flip-session")!).state.trays["fixture-user:fixtureproj"].length);
        await expect.poll(persistedCount).toBe(status === 403 ? 0 : 3);
        await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
    });
}

test("network recovery removes newly inaccessible tray cards without changing the active detail", async ({ page }) => {
    await mockBoardApi(page);
    await seedTray(page, 3);
    let revoked = false;
    let validations = 0;
    await page.route("**/dashboard/cards/available", async (route) => {
        if (route.request().method() === "OPTIONS") {
            await route.fulfill({ status: 204, headers: corsHeaders(route) });
            return;
        }
        validations++;
        await fulfillWithCors(route, {
            cards: (revoked ? ["other000001"] : ["other000000", "other000001", "other000002"]).map((card_uid) => ({
                project_uid: "fixtureproj",
                card_uid,
            })),
        });
    });
    await page.goto(FIXTURE);
    await expect(page.getByRole("button", { name: "Flip card", exact: true })).toBeVisible();
    await expect.poll(() => validations).toBeGreaterThan(0);
    revoked = true;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect
        .poll(() =>
            page.evaluate(() =>
                JSON.parse(sessionStorage.getItem("langboard-card-flip-session")!).state.trays["fixture-user:fixtureproj"].map(
                    (card: { uid: string }) => card.uid
                )
            )
        )
        .toEqual(["other000001"]);
    await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "Fixture card", exact: true })).toBeVisible();
});

test("reduced-motion keeps the compact tray keyboard accessible", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 844 });
    await mockBoardApi(page);
    await seedTray(page, 3);
    await page.goto(FIXTURE);
    const button = page.getByRole("button", { name: "Flipped cards · 3", exact: true });
    await button.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Restore Card other000000", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(button).toHaveAttribute("aria-expanded", "false");
    await expect(button).toBeFocused();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
});

test("retained comment draft has a tray dot and restore expands from the chip", async ({ page }) => {
    await mockBoardApi(page);
    await seedTray(page, 1);
    await page.addInitScript(() => sessionStorage.setItem("comment-fixture-user-fixtureproj-other000000", "Unsaved comment"));
    await page.goto(FIXTURE);
    const chip = page.locator("[data-card-flip-item=other000000]");
    await expect(chip.locator("[data-card-flip-unsaved]")).toBeVisible();
    await page.getByRole("button", { name: "Restore Card other000000", exact: true }).click();
    const viewer = page.locator("[data-card-viewer]");
    await expect(viewer).toHaveCount(1);
    await expect.poll(() => viewer.evaluate((element) => element.style.getPropertyValue("--card-origin-transform"))).toContain("translate(");
});

test("unsynchronized restored deadline cannot save and cancellation restores the confirmed value", async ({ page }) => {
    await mockBoardApi(page);
    let writes = 0;
    await page.route("**/board/fixtureproj/card/fixturecard/details", async (route) => {
        if (route.request().method() !== "OPTIONS") writes++;
        await fulfillWithCors(route, {});
    });
    await page.addInitScript(() => {
        sessionStorage.setItem(
            "langboard-card-flip-drafts",
            JSON.stringify({
                state: { drafts: { "fixture-user:fixtureproj:fixturecard": { deadline_at: "2030-06-15T12:00:00Z" } } },
                version: 0,
            })
        );
    });
    await page.goto(FIXTURE);
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Syncing draft...", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    expect(writes).toBe(0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add deadline", exact: true })).toBeVisible();
    expect(writes).toBe(0);
});

test("restoring a suspended card resumes its unsaved title edit", async ({ page }) => {
    await mockBoardApi(page);
    await page.addInitScript(() => {
        sessionStorage.setItem(
            "langboard-card-flip-drafts",
            JSON.stringify({ state: { drafts: { "fixture-user:fixtureproj:fixturecard": { title: "Suspended title" } } }, version: 0 })
        );
    });
    await page.goto(FIXTURE);
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Flip card", exact: true }).click();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(0);
    expect(
        await page.evaluate(
            () => JSON.parse(sessionStorage.getItem("langboard-card-flip-drafts")!).state.drafts["fixture-user:fixtureproj:fixturecard"].title
        )
    ).toBe("Suspended title");
    await expect(page.locator("[data-card-flip-unsaved]")).toBeVisible();
    await page.getByRole("button", { name: "Restore Fixture card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
});

for (const width of [390, 1280]) {
    test(`tray drag changes order without restoring a card at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await mockBoardApi(page);
        await seedTray(page, 3);
        await page.goto(FIXTURE);
        const overflow = page.getByRole("button", { name: "Flipped cards · 3", exact: true });
        const vertical = width < 768;
        if (vertical) await overflow.waitFor({ state: "visible" });
        if (vertical) await overflow.click();
        const first = await page.locator("[data-card-flip-item=other000000] [data-card-flip-drag-handle]").boundingBox();
        const last = await page.locator("[data-card-flip-item=other000002]").boundingBox();
        await page.mouse.move(first!.x + first!.width / 2, first!.y + first!.height / 2);
        await page.mouse.down();
        await page.mouse.move(vertical ? last!.x + 12 : last!.x + last!.width / 2 + 8, vertical ? last!.y + last!.height / 2 + 8 : last!.y + 12, {
            steps: 20,
        });
        await page.mouse.up();
        await expect
            .poll(() =>
                page.evaluate(() =>
                    JSON.parse(sessionStorage.getItem("langboard-card-flip-session")!).state.trays["fixture-user:fixtureproj"].map(
                        (card: { uid: string }) => card.uid
                    )
                )
            )
            .toEqual(["other000001", "other000002", "other000000"]);
        await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
    });
}

test("touch drag reorders compact tray without restoring", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockBoardApi(page);
    await seedTray(page, 3);
    await page.goto(FIXTURE);
    await page.getByRole("button", { name: "Flipped cards · 3", exact: true }).click();
    const first = await page.locator("[data-card-flip-item=other000000] [data-card-flip-drag-handle]").boundingBox();
    const last = await page.locator("[data-card-flip-item=other000002]").boundingBox();
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: first!.x + 10, y: first!.y + 15 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: last!.x + 10, y: last!.y + last!.height / 2 + 8 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect
        .poll(() =>
            page.evaluate(() =>
                JSON.parse(sessionStorage.getItem("langboard-card-flip-session")!).state.trays["fixture-user:fixtureproj"].map(
                    (c: { uid: string }) => c.uid
                )
            )
        )
        .toEqual(["other000001", "other000002", "other000000"]);
    await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
});

test("sidebar collapse and expansion preserve the open card", async ({ page }) => {
    await mockBoardApi(page);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto(`${FIXTURE}?sidebar`);
    const viewer = page.locator("[data-card-viewer]");
    const sidebar = page.locator("[data-workbench-sidebar]");
    await expect(page.locator("[data-card-surface]")).toBeVisible();
    await sidebar.getByRole("button", { name: /Collapse|접기/ }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect(viewer).toBeVisible();
    await sidebar.getByRole("button", { name: /Expand|펼치기/ }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect(viewer).toBeVisible();
    await sidebar.getByRole("button", { name: /Collapse|접기/ }).press("Enter");
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await sidebar.getByRole("button", { name: /Expand|펼치기/ }).press("Enter");
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await sidebar.getByRole("button", { name: /Collapse|접기/ }).press("Space");
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect(viewer).toBeVisible();
});

for (const initiallyCollapsed of [false, true]) {
    test(`sidebar icon pointer targets preserve the open card from collapsed=${initiallyCollapsed}`, async ({ page }) => {
        await mockBoardApi(page);
        await page.setViewportSize({ width: 1024, height: 768 });
        await page.goto(`${FIXTURE}?sidebar${initiallyCollapsed ? "&sidebarCollapsed" : ""}`);
        const viewer = page.locator("[data-card-viewer]");
        const sidebar = page.locator("[data-workbench-sidebar]");
        await expect(page.locator("[data-card-surface]")).toBeVisible();
        for (const collapsed of [!initiallyCollapsed, initiallyCollapsed]) {
            const button = sidebar.getByRole("button", { name: collapsed ? /Collapse|접기/ : /Expand|펼치기/ });
            await button.evaluate((el) =>
                el.addEventListener(
                    "pointerdown",
                    (event) => {
                        el.setAttribute("data-pointer-target", (event.target as Element).tagName.toLowerCase());
                    },
                    { once: true }
                )
            );
            await button.locator("svg").click();
            await expect(sidebar.locator("button[data-pointer-target]")).toHaveAttribute("data-pointer-target", /^(svg|path|polyline)$/);
            await expect(sidebar).toHaveAttribute("data-collapsed", String(collapsed));
            await expect
                .poll(() => sidebar.evaluate((el) => el.getAnimations().filter((animation) => animation.playState === "running").length))
                .toBe(0);
            await expect(viewer).toBeVisible();
        }
    });
}

test("dashboard deletion removes flipped cards without a loaded model", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/CardFlipDeletion.fixture.html");
    await expect(page.getByRole("status")).toContainText("PASS unloaded card removed");
});

for (const reduced of [false, true]) {
    test(`Flip and restore use bounded Motion transitions reduced=${reduced}`, async ({ page }) => {
        await page.emulateMedia({ reducedMotion: reduced ? "reduce" : "no-preference" });
        await page.addInitScript(() => {
            const original = Element.prototype.animate;
            const records: unknown[] = [];
            (window as unknown as { __flipMotion: unknown[] }).__flipMotion = records;
            Element.prototype.animate = function (frames, options) {
                if (this.hasAttribute("data-card-viewer")) records.push({ frames, options });
                return original.call(this, frames, options);
            };
        });
        await mockBoardApi(page);
        await seedTray(page, 1);
        await page.goto(FIXTURE);
        await expect(page.getByRole("heading", { name: "Fixture card", exact: true })).toBeVisible();
        await expect
            .poll(() => page.locator("[data-card-viewer]").evaluate((el) => el.getAnimations().filter((a) => a.playState === "running").length))
            .toBe(0);
        await page.getByRole("button", { name: "Flip card", exact: true }).click();
        await expect(page.locator("[data-card-viewer]")).toHaveCount(0);
        await page.getByRole("button", { name: "Restore Fixture card", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Fixture card", exact: true })).toBeVisible();
        await expect
            .poll(() => page.locator("[data-card-viewer]").evaluate((el) => el.getAnimations().filter((a) => a.playState === "running").length))
            .toBe(0);
        const records = await page.evaluate(
            () => (window as unknown as { __flipMotion: Array<{ options: { duration: number }; frames: unknown }> }).__flipMotion
        );
        if (reduced) expect(records).toHaveLength(0);
        else {
            expect(records.length).toBeGreaterThanOrEqual(2);
            records.forEach((record) => expect(record.options.duration).toBe(180));
        }
        await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
        await expect(page.locator("[data-card-viewer]")).toHaveCSS("opacity", "1");
    });
}

test("actual desktop Graph route retains access to flipped cards", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width: 1440, height: 1080 });
    await mockBoardApi(page);
    await page.route(
        (url) =>
            url.port === "5381" &&
            !url.pathname.startsWith("/board/fixtureproj") &&
            !url.pathname.startsWith("/auth") &&
            !url.pathname.startsWith("/activity/") &&
            url.pathname !== "/dashboard/cards/available",
        (route) => fulfillWithCors(route, { metadata: {}, projects: [], labels: [], approvals: [], count: 0 })
    );
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj/cards",
        (route) =>
            fulfillWithCors(route, {
                cards: [],
                columns: [],
                checklists: [],
                global_relationships: [],
                column_bot_scopes: [],
                column_bot_schedules: [],
            })
    );
    await seedTray(page, 3);
    await page.goto(FIXTURE + "?fullGraph");
    await expect(page.locator("[data-card-flip-tray]")).toBeVisible();
    const restore = page.getByRole("button", { name: "Restore Card other000000", exact: true });
    if (!(await restore.isVisible())) await page.getByRole("button", { name: "Flipped cards · 3", exact: true }).click();
    await expect(restore).toBeVisible();
    expect(errors).toEqual([]);
});

test("an authorized detail omitted from the board projection can return to the Flip tray", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await mockBoardApi(page);
    await page.route(
        (url) =>
            url.port === "5381" &&
            !url.pathname.startsWith("/board/fixtureproj") &&
            !url.pathname.startsWith("/auth") &&
            !url.pathname.startsWith("/activity/") &&
            url.pathname !== "/dashboard/cards/available",
        (route) => fulfillWithCors(route, { metadata: {}, projects: [], labels: [], approvals: [], count: 0, active_work: [] })
    );
    const columns = [
        { uid: "fixture-column", project_uid: "fixtureproj", name: "Ready", order: 0, is_archive: false },
        { uid: "fixture-archive", project_uid: "fixtureproj", name: "Archive", order: 1, is_archive: true },
    ];
    // The availability endpoint authorizes other000000, but the current board
    // projection intentionally excludes it (for example an archived detail).
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj/cards",
        (route) =>
            fulfillWithCors(route, {
                cards: [FIXTURE_CARD],
                columns,
                checklists: [],
                global_relationships: [],
                column_bot_scopes: [],
                column_bot_schedules: [],
            })
    );
    await page.route("**/board/fixtureproj/columns", (route) => fulfillWithCors(route, { columns }));
    await seedTray(page, 1);
    await page.goto(`${FIXTURE}?fullBoard`);
    await page.getByRole("button", { name: "Restore Card other000000", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("heading", { name: "Card other000000", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Flip card", exact: true }).click();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Restore Card other000000", exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});

test("compact board refresh does not clear a hydrated card description", async ({ page }) => {
    await mockBoardApi(page);
    const body = "Full detail survives compact board refresh.";
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj/card/fixturecard",
        (route) =>
            fulfillWithCors(route, {
                card: { ...FIXTURE_CARD, description: { content: body }, has_description: true },
                attachments: [],
                checklists: [],
                global_relationships: [],
                project_columns: [],
                project_labels: [],
                bot_scopes: [],
            })
    );
    let summaryReads = 0;
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj/cards",
        (route) => {
            if (route.request().method() !== "OPTIONS") summaryReads++;
            const { description: _description, ...summary } = FIXTURE_CARD;
            return fulfillWithCors(route, {
                cards: [{ ...summary, has_description: true }],
                columns: [],
                checklists: [],
                global_relationships: [],
                column_bot_scopes: [],
                column_bot_schedules: [],
            });
        }
    );
    await page.goto(`${FIXTURE}?compactSummary=1`);
    await expect(page.getByText(body, { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Refresh compact summary", exact: true }).press("Enter");
    await expect.poll(() => summaryReads).toBe(1);
    await expect(page.getByText(body, { exact: true }).first()).toBeVisible();
});

for (const width of [1920, 390]) {
    test(`comments mount once at ${width}px and unmount while closed`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await mockBoardApi(page);
        await page.route("**/activity/project/fixtureproj/card/fixturecard/timeline**", (route) => {
            return fulfillWithCors(route, {
                records: [
                    {
                        uid: "single-comment",
                        kind: "comment",
                        evidence_ref: "comment:single-comment",
                        created_at: NOW_ISO,
                        data: {
                            uid: "single-comment",
                            card_uid: FIXTURE_CARD.uid,
                            created_at: NOW_ISO,
                            updated_at: NOW_ISO,
                            content: { content: "Single comment surface" },
                            user: FIXTURE_USER,
                            is_edited: false,
                            reactions: {},
                        },
                    },
                ],
                next_cursor: null,
            });
        });
        await page.goto(FIXTURE);
        const comments = page.locator("[data-card-comment-uid=single-comment]");
        const toggle = page.getByRole("button", { name: "Comments", exact: true });
        await expect(toggle).toBeVisible();
        await expect(comments).toHaveCount(0);
        await toggle.click();
        await expect(comments).toHaveCount(1);
        await expect(comments).toBeVisible();
        await toggle.click();
        await expect(comments).toHaveCount(0);
        await toggle.click();
        await expect(comments).toHaveCount(1);
    });
}

test("unsynchronized restored description cannot submit a card details patch", async ({ page }) => {
    await mockBoardApi(page);
    let writes = 0;
    await page.route("**/board/fixtureproj/card/fixturecard/details", async (route) => {
        if (route.request().method() !== "OPTIONS") writes++;
        await fulfillWithCors(route, {});
    });
    await page.addInitScript(() => {
        sessionStorage.setItem(
            "langboard-card-flip-drafts",
            JSON.stringify({
                state: { drafts: { "fixture-user:fixtureproj:fixturecard": { description: { content: "Retained unsaved description" } } } },
                version: 0,
            })
        );
    });
    await page.goto(FIXTURE);
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Flip card", exact: true }).click();
    await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
    expect(
        await page.evaluate(
            () =>
                JSON.parse(sessionStorage.getItem("langboard-card-flip-drafts")!).state.drafts["fixture-user:fixtureproj:fixturecard"].description
                    .content
        )
    ).toBe("Retained unsaved description");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    expect(writes).toBe(0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
});

for (const width of [360, 390]) {
    test(`mobile viewer uses one fullscreen mode at ${width}px and follows available width`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.emulateMedia({ reducedMotion: "reduce" });
        await mockBoardApi(page);
        await page.goto(FIXTURE);
        const viewer = page.locator("[data-card-viewer]");
        await expect(page.getByText("Fixture card", { exact: true }).first()).toBeVisible();
        const expectFullScreen = async (expectedWidth: number, expectedHeight: number) => {
            await expect
                .poll(async () =>
                    viewer.evaluate((element) => {
                        const r = element.getBoundingClientRect();
                        return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
                    })
                )
                .toEqual([0, 0, expectedWidth, expectedHeight]);
            await expect(page.getByRole("button", { name: /^(Expand|Collapse)$/ })).toHaveCount(0);
        };
        await expectFullScreen(width, 844);
        await page.locator("body").dispatchEvent("pointerdown", { pointerType: "touch", button: 0 });
        await expect(viewer).toBeVisible();
        await page.setViewportSize({ width: 720, height: 540 });
        await expectFullScreen(720, 540);
        await page.setViewportSize({ width: 1280, height: 800 });
        await expect(page.getByRole("button", { name: "Expand", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Expand", exact: true }).click();
        await expect(page.getByRole("button", { name: "Collapse", exact: true })).toBeVisible();
        await page.setViewportSize({ width, height: 600 });
        await expectFullScreen(width, 600);
        await page.setViewportSize({ width: 1280, height: 800 });
        await expect(page.getByRole("button", { name: "Collapse", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await expect(viewer).toHaveCount(0);
    });
}

for (const dismissal of ["button", "background"]) {
    test(`${dismissal} dismissal plays the same return animation before unmount`, async ({ page }) => {
        await mockBoardApi(page);
        await page.goto(FIXTURE);
        const viewer = page.locator("[data-card-viewer]");
        await expect(page.locator("[data-card-surface]")).toBeVisible();
        await expect(viewer).toHaveAttribute("data-card-viewer-opened", "true");
        await expect
            .poll(() => viewer.evaluate((element) => element.getAnimations().some((animation) => animation.playState === "running")))
            .toBe(false);
        if (dismissal === "button") await page.getByRole("button", { name: "Close", exact: true }).click();
        else {
            const surface = await page.locator("[data-card-surface]").boundingBox();
            const nav = await page.locator("[data-floating-nav-content]").locator("button").first().boundingBox();
            await page.mouse.click(surface!.x + 8, (surface!.y + surface!.height + nav!.y) / 2);
        }
        await expect
            .poll(
                () =>
                    viewer.evaluate((element) =>
                        element.getAnimations().some((animation) => {
                            const frames = (animation.effect as KeyframeEffect)?.getKeyframes();
                            return frames?.length && Number(frames[0].opacity) === 1 && Number(frames.at(-1)?.opacity) === 0;
                        })
                    ),
                { intervals: [10, 20, 30] }
            )
            .toBeTruthy();
        await expect(viewer).toHaveCount(0);
    });
}

test("board rail toggles the active panel and reopens a manually collapsed context", async ({ page }) => {
    await mockBoardApi(page);
    await page.route(
        (url) =>
            url.port === "5381" &&
            !url.pathname.startsWith("/board/fixtureproj") &&
            !url.pathname.startsWith("/auth") &&
            !url.pathname.startsWith("/activity/") &&
            url.pathname !== "/dashboard/cards/available",
        (route) => fulfillWithCors(route, { metadata: {}, projects: [], labels: [], approvals: [], count: 0, active_work: [] })
    );
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj/cards",
        (route) =>
            fulfillWithCors(route, {
                cards: [FIXTURE_CARD],
                columns: [],
                checklists: [],
                global_relationships: [],
                column_bot_scopes: [],
                column_bot_schedules: [],
            })
    );
    await page.setViewportSize({ width: 1457, height: 954 });
    await page.goto(`${FIXTURE}?fullBoard`);
    await expect(page.locator("[data-workbench-explorer]")).toBeVisible();
    const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
    const explorer = rail.getByRole("button", { name: "Explorer", exact: true });
    const sidebar = page.locator("[data-workbench-sidebar]").filter({ has: page.locator("[data-workbench-explorer]") });
    await explorer.click();
    await expect(sidebar).toBeHidden();
    await explorer.click();
    await expect(sidebar).toBeVisible();
    await sidebar.getByRole("button", { name: "Collapse", exact: true }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await explorer.click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect(page.locator("[data-workbench-explorer]")).toBeVisible();
});

test("dashboard owns home first, omits quick switcher and toggles its context", async ({ page }) => {
    await mockBoardApi(page);
    await page.route(
        (url) =>
            url.port === "5381" &&
            !url.pathname.startsWith("/auth") &&
            !url.pathname.startsWith("/activity/") &&
            url.pathname !== "/dashboard/cards/available",
        (route) => fulfillWithCors(route, { projects: [], columns: [], metadata: {}, pinned_apps: [], revision: 0 })
    );
    await page.setViewportSize({ width: 1457, height: 954 });
    await page.addInitScript(() => history.replaceState({}, "", "/dashboard/projects/all?fullDashboard"));
    await page.goto(`${FIXTURE}?fullDashboard`);
    const rail = page.getByRole("navigation", { name: "Workspace", exact: true });
    const home = rail.getByRole("button").first();
    await expect(home).toHaveAccessibleName("Projects");
    await expect(home.locator("svg")).toHaveClass(/lucide-house/);
    await expect(rail.getByRole("button", { name: "Quick switcher", exact: true })).toHaveCount(0);
    const explorer = rail.getByRole("button", { name: "Explorer", exact: true });
    const sidebar = page.locator("[data-workbench-sidebar]").filter({ has: page.locator("[data-workbench-explorer]") });
    await expect(sidebar).toBeVisible();
    await explorer.click();
    await expect(sidebar).toBeHidden();
    await explorer.click();
    await expect(sidebar).toBeVisible();
    await sidebar.getByRole("button", { name: "Collapse", exact: true }).click();
    await explorer.click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
});

test("mobile board rail closes its active Explorer on a second press", async ({ page }) => {
    await mockBoardApi(page);
    await page.route(
        (url) =>
            url.port === "5381" &&
            !url.pathname.startsWith("/board/fixtureproj") &&
            !url.pathname.startsWith("/auth") &&
            !url.pathname.startsWith("/activity/") &&
            url.pathname !== "/dashboard/cards/available",
        (route) => fulfillWithCors(route, { metadata: {}, projects: [], labels: [], approvals: [], count: 0, active_work: [] })
    );
    await page.route(
        (url) => url.port === "5381" && url.pathname === "/board/fixtureproj/cards",
        (route) =>
            fulfillWithCors(route, {
                cards: [],
                columns: [],
                checklists: [],
                global_relationships: [],
                column_bot_scopes: [],
                column_bot_schedules: [],
            })
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${FIXTURE}?fullBoard`);
    const explorer = page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name: "Explorer", exact: true });
    const panel = page.getByRole("complementary", { name: "Explorer", exact: true });
    await explorer.press("Enter");
    await expect(panel).toBeVisible();
    await explorer.press("Enter");
    await expect(panel).toHaveCount(0);
    await explorer.press("Enter");
    await expect(panel).toBeVisible();
});

for (const paneScope of [false, true]) {
    test(`card close and Flip restore preserve pane filters rather than browser-global search scope=${paneScope}`, async ({ page }) => {
        await mockBoardApi(page);
        await page.route("**/dashboard/cards/available", (route) => {
            const cards = route.request().method() === "OPTIONS" ? [] : route.request().postDataJSON().cards;
            return fulfillWithCors(route, { cards });
        });
        await page.goto(`${FIXTURE}?scopedSearch=1${paneScope ? "&paneScope=1" : ""}`);
        const location = page.locator("[data-scoped-location]");
        await expect(location).toHaveText("/board/fixtureproj/fixturecard?filters=keyword:pane-only");
        await page.getByRole("button", { name: "Flip card", exact: true }).click();
        await expect(page.locator("[data-card-viewer]")).toHaveCount(0);
        await expect(location).toHaveText("/board/fixtureproj?filters=keyword:pane-only");
        await page.getByRole("button", { name: "Restore Fixture card", exact: true }).click();
        await expect(page.locator("[data-card-viewer]")).toHaveCount(1);
        await expect(location).toHaveText("/board/fixtureproj/fixturecard?filters=keyword:pane-only");
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await expect(location).toHaveText("/board/fixtureproj?filters=keyword:pane-only");
    });
}
