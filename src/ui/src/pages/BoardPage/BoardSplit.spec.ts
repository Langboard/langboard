import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page, withCollection = false, tallBoard = false) {
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
    let collectionWrites = 0;
    let savedCollection = {
        revision: 0,
        collections: [{ uid: "00000000-0000-4000-8000-000000000001", name: "Fixture collection", board_uids: [] as string[] }],
    };
    const projects = ["left", "right", "third", ...(withCollection ? ["00000000001"] : [])].map((uid) => ({
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
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const path = new URL(route.request().url()).pathname;
            const origin = route.request().headers().origin || "http://127.0.0.1:4196";
            const headers = {
                "access-control-allow-origin": origin,
                "access-control-allow-credentials": "true",
                "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                "access-control-allow-headers": "authorization,content-type,content-encoding",
            };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            const uid = path.split("/")[2];
            let json: unknown = {};
            if (path === "/account/project-collections" && withCollection) {
                if (route.request().method() === "PUT") {
                    collectionWrites++;
                    const input = route.request().postDataJSON();
                    expect(input.expected_revision).toBe(savedCollection.revision);
                    savedCollection = { revision: savedCollection.revision + 1, collections: input.collections };
                }
                json = savedCollection;
            } else if (path.startsWith("/auth/")) json = { access_token: "fixture", user, bots: [] };
            else if (path === `/board/${uid}`)
                json = { project: projects.find((project) => project.uid === uid), project_bot_scopes: [], project_bot_schedules: [] };
            else if (path === `/board/${uid}/cards`)
                json = {
                    cards: Array.from({ length: tallBoard ? 30 : 1 }, (_, index) => ({
                        uid: index ? `${uid}-card-${index}` : `${uid}-card`,
                        project_uid: uid,
                        project_column_uid: `${uid}-column-9`,
                        project_column_name: `${uid} column 9`,
                        title: index ? `${uid} tall card ${index}` : `${uid} draggable card`,
                        created_at: now,
                        updated_at: now,
                        order: 0,
                        count_comment: 0,
                        member_uids: [],
                        current_auth_role_actions: ["*"],
                        project_members: [],
                        labels: [],
                        relationships: [],
                        has_description: false,
                    })),
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
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    await expect(page.getByText("left column 0", { exact: true }).first()).toBeVisible({ timeout: 45_000 });
    await expect(page.locator("[data-board-pane-footer]")).toHaveCount(0);
    return () => ({ collectionWrites, savedCollection });
}

test("explorer opens two real boards with one shell; either close preserves survivor DOM", async ({ page }) => {
    await setup(page);
    if (!(await page.locator("[data-workbench-explorer]").isVisible())) await page.getByRole("button", { name: "Explorer", exact: true }).click();
    const split = page.getByRole("button", { name: "Open right board in split view" }).first();
    await split.hover();
    await expect(split).toHaveCSS("opacity", "1");
    await page.mouse.move(1100, 50);
    await split.focus();
    await expect(split).toBeFocused();
    await expect(split).toHaveCSS("opacity", "1");
    await page.keyboard.press("Enter");
    const panes = page.locator("[data-board-pane]");
    await expect(panes).toHaveCount(2);
    await expect(panes.nth(1).locator("[data-board-pane-footer]")).toContainText("right board");
    await expect(page.getByRole("navigation", { name: "Workspace", exact: true })).toHaveCount(1);
    await expect(page.locator("[data-board-pane]").nth(1).getByText("right column 0", { exact: true }).first()).toBeVisible();
    const rightID = await panes.nth(1).getAttribute("data-board-pane");
    const right = page.locator(`[data-board-pane="${rightID}"]`);
    await right.evaluate((element) => {
        (element as HTMLElement).dataset.survivor = "retained";
    });
    await right.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("textbox", { name: "Keyword", exact: true }).fill("right-only");
    await expect(page.getByRole("textbox", { name: "Keyword", exact: true })).toHaveValue("right-only");
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-native-location]")).toContainText("/board/right?filters=");
    await expect(panes.first().getByRole("button", { name: "Filters", exact: true })).toHaveText("Filters");
    await right.getByRole("button", { name: "Add a card", exact: true }).first().click();
    const composer = right.getByPlaceholder("Enter a title", { exact: true });
    await composer.fill("Surviving composer draft");
    await expect
        .poll(() => composer.evaluate((element) => element.getAnimations().some((animation) => animation.effect?.getTiming().duration === 700)))
        .toBe(true);
    await composer.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    const rightViewport = right.locator("[id^=board-scrollport-]").first();
    await rightViewport.evaluate((element) => {
        element.scrollLeft = 200;
    });
    await expect.poll(() => rightViewport.evaluate((element) => element.scrollLeft)).toBeGreaterThan(100);
    await expect
        .poll(() =>
            panes
                .first()
                .locator("[id^=board-scrollport-]")
                .first()
                .evaluate((element) => element.scrollLeft)
        )
        .toBe(0);
    await expect(right).toHaveAttribute("data-board-pane-focused", "true");
    await panes.first().getByRole("button", { name: "Close left board", exact: true }).click();
    await expect(panes).toHaveCount(1);
    await expect(panes.first()).toHaveAttribute("data-survivor", "retained");
    await expect(panes.first().getByPlaceholder("Enter a title", { exact: true })).toHaveValue("Surviving composer draft");
    await expect(panes.first().getByRole("button", { name: "Filters", exact: true })).toHaveText("Filters (1)");
    await expect.poll(() => rightViewport.evaluate((element) => element.scrollLeft)).toBeGreaterThan(100);
});

test("board-reference drop opens split; maximum two and mobile cannot split", async ({ page }) => {
    await setup(page);
    const drop = async (uid: string) => {
        const transfer = await page.evaluateHandle((value) => {
            const data = new DataTransfer();
            data.setData("application/x-langboard-board-reference", value);
            return data;
        }, uid);
        await page.locator("[data-board-pane]").first().dispatchEvent("drop", { dataTransfer: transfer });
    };
    await drop("right");
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
    await drop("third");
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
    await page.locator("[data-board-pane]").nth(1).getByRole("button", { name: "Close right board", exact: true }).click();
    await expect(page.locator("[data-board-pane]")).toHaveCount(1);
    await page.setViewportSize({ width: 390, height: 844 });
    await drop("right");
    await expect(page.locator("[data-board-pane]")).toHaveCount(1);
});

test("native explorer navigation and browser Back focus existing boards without duplicate mounts", async ({ page }) => {
    await setup(page);
    const transfer = await page.evaluateHandle(() => {
        const data = new DataTransfer();
        data.setData("application/x-langboard-board-reference", "right");
        return data;
    });
    await page.locator("[data-board-pane]").first().dispatchEvent("drop", { dataTransfer: transfer });
    await expect(page.locator("[data-board-pane-footer]")).toHaveCount(2);
    if (!(await page.locator("[data-workbench-explorer]").isVisible())) await page.getByRole("button", { name: "Explorer", exact: true }).click();
    const panes = page.locator("[data-board-pane]");
    await panes.first().evaluate((element) => {
        (element as HTMLElement).dataset.nativeRetained = "left";
    });
    await panes.nth(1).evaluate((element) => {
        (element as HTMLElement).dataset.nativeRetained = "right";
    });
    const explorer = page.locator("[data-workbench-explorer]");
    await explorer.getByRole("button", { name: "right board", exact: true }).first().click();
    await expect(page).toHaveURL(/\/board\/right$/);
    await expect(panes.nth(1)).toHaveAttribute("data-board-pane-focused", "true");
    await explorer.getByRole("button", { name: "left board", exact: true }).first().click();
    await expect(page).toHaveURL(/\/board\/left$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/board\/right$/);
    await expect(panes.nth(1)).toHaveAttribute("data-board-pane-focused", "true");
    await expect(panes.first()).toHaveAttribute("data-native-retained", "left");
    await expect(panes.nth(1)).toHaveAttribute("data-native-retained", "right");
    await expect(panes.first().locator("[data-board-pane-footer]")).toContainText("left board");
    await expect(panes.nth(1).locator("[data-board-pane-footer]")).toContainText("right board");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("[data-board-pane]:visible")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Close right board", exact: true })).toHaveCount(0);
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.locator("[data-board-pane]:visible")).toHaveCount(2);
    await expect(panes.first()).toHaveAttribute("data-native-retained", "left");
    await expect(panes.nth(1)).toHaveAttribute("data-native-retained", "right");
});

test("desktop split suspends on mobile without losing either composer, focus or retained DOM", async ({ page }) => {
    let submissions = 0;
    page.on("request", (request) => {
        if (request.method() === "POST" && /\/board\/[^/]+\/card$/.test(new URL(request.url()).pathname)) submissions++;
    });
    await setup(page);
    const transfer = await page.evaluateHandle(() => {
        const data = new DataTransfer();
        data.setData("application/x-langboard-board-reference", "right");
        return data;
    });
    await page.locator("[data-board-pane]").first().dispatchEvent("drop", { dataTransfer: transfer });
    const panes = page.locator("[data-board-pane]");
    await expect(panes.nth(1).locator("[data-board-pane-footer]")).toContainText("right board");
    for (const index of [0, 1]) {
        const pane = panes.nth(index);
        await pane.getByRole("button", { name: "Add a card", exact: true }).first().click();
        const composer = pane.getByPlaceholder("Enter a title", { exact: true });
        await composer.fill(`Pane ${index} unsaved draft`);
        await pane.evaluate((element, id) => element.setAttribute("data-mobile-retained", String(id)), index);
        await composer.evaluate((element, id) => element.setAttribute("data-mobile-retained", String(id)), index);
    }
    for (const focusedIndex of [0, 1]) {
        const focused = panes.nth(focusedIndex);
        const composer = focused.getByPlaceholder("Enter a title", { exact: true });
        await composer.focus();
        await expect(focused).toHaveAttribute("data-board-pane-focused", "true");
        await page.setViewportSize({ width: 390, height: 844 });
        await expect(page.locator("[data-board-pane]:visible")).toHaveCount(1);
        await expect(focused).toBeVisible();
        await expect(composer).toBeFocused();
        await expect(page.locator("[data-board-pane-footer]:visible")).toHaveCount(0);
        await expect(page.getByRole("button", { name: /in split view/ })).toHaveCount(0);
        await expect(page.getByRole("navigation", { name: "Workspace", exact: true })).toHaveCount(1);
        await page.setViewportSize({ width: 1440, height: 900 });
        await expect(page.locator("[data-board-pane]:visible")).toHaveCount(2);
        await expect(composer).toBeFocused();
        for (const index of [0, 1]) {
            await expect(panes.nth(index)).toHaveAttribute("data-mobile-retained", String(index));
            const draft = panes.nth(index).getByPlaceholder("Enter a title", { exact: true });
            await expect(draft).toHaveAttribute("data-mobile-retained", String(index));
            await expect(draft).toHaveValue(`Pane ${index} unsaved draft`);
        }
        expect(submissions).toBe(0);
    }
});

test("two board composers retain independent drafts and opposite-pane close never submits", async ({ page }) => {
    let submissions = 0;
    page.on("request", (request) => {
        if (request.method() === "POST" && /\/board\/[^/]+\/card$/.test(new URL(request.url()).pathname)) submissions++;
    });
    await setup(page);
    const transfer = await page.evaluateHandle(() => {
        const data = new DataTransfer();
        data.setData("application/x-langboard-board-reference", "right");
        return data;
    });
    await page.locator("[data-board-pane]").first().dispatchEvent("drop", { dataTransfer: transfer });
    const left = page.locator("[data-board-pane=board-pane-0]");
    const right = page.locator("[data-board-pane=board-pane-1]");
    await expect(right.locator("[data-board-pane-footer]")).toContainText("right board");
    await left.getByRole("button", { name: "Add a card", exact: true }).first().click();
    await left.getByPlaceholder("Enter a title", { exact: true }).fill("Left independent draft");
    await right.getByRole("button", { name: "Add a card", exact: true }).first().click();
    await right.getByPlaceholder("Enter a title", { exact: true }).fill("Right independent draft");
    await expect(left.getByPlaceholder("Enter a title", { exact: true })).toHaveValue("Left independent draft");
    await expect(right.getByPlaceholder("Enter a title", { exact: true })).toHaveValue("Right independent draft");
    await right.getByRole("button", { name: "Close right board", exact: true }).click();
    await expect(page.locator("[data-board-pane]")).toHaveCount(1);
    await expect(left.getByPlaceholder("Enter a title", { exact: true })).toHaveValue("Left independent draft");
    expect(submissions).toBe(0);
});

async function pullBoard(
    page: Page,
    options: { edge?: "left" | "right"; inside?: boolean; dx?: number; dy?: number; cancel?: boolean; blocked?: boolean } = {}
) {
    const viewport = page.locator("[data-board-pane]").first().locator("[id^=board-scrollport-]").first();
    const edge = options.edge ?? "right";
    await viewport.evaluate((element, side) => {
        element.scrollLeft = side === "right" ? element.scrollWidth : 0;
    }, edge);
    const bounds = (await viewport.boundingBox())!;
    const x = edge === "right" ? bounds.x + bounds.width - (options.inside ? 90 : 12) : bounds.x + 12;
    const y = bounds.y + bounds.height - 50;
    if (options.blocked) {
        await viewport.evaluate((element) => element.setAttribute("data-block-board-panning", "true"));
    }
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + (options.dx ?? (edge === "right" ? -120 : 120)), y + (options.dy ?? 0), { steps: 8 });
    if (options.cancel) await viewport.dispatchEvent("pointercancel", { pointerId: 1 });
    await page.mouse.up();
    if (options.blocked) await viewport.evaluate((element) => element.removeAttribute("data-block-board-panning"));
}

test("right boundary pull chooses an accessible board; desktop themes and two-pane ceiling", async ({ page }) => {
    await setup(page);
    for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => {
            document.documentElement.classList.toggle("dark", value === "dark");
        }, theme);
        await pullBoard(page);
        await expect(page.getByRole("dialog")).toBeVisible();
        await expect(page.getByRole("option", { name: "left board", exact: true })).toHaveCount(0);
        await expect(page.getByRole("option", { name: "right board", exact: true })).toBeVisible();
        await expect.poll(() => page.getByRole("dialog").evaluate((element) => getComputedStyle(element.parentElement!).opacity)).toBe("1");
        await page.screenshot({ path: `/tmp/langboard-split-chooser-${theme}.png` });
        if (theme === "light") await page.keyboard.press("Escape");
    }
    await page.getByRole("combobox", { name: "Search projects", exact: true }).fill("right");
    await expect(page.getByRole("option", { name: "third board", exact: true })).toHaveCount(0);
    await page.getByRole("option", { name: "right board", exact: true }).click();
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
    await expect(page.locator("[data-board-pane]").nth(1).getByText("right column 0", { exact: true }).first()).toBeVisible();
    await pullBoard(page);
    await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("right pull ignores inside, vertical, short, reversed, cancelled and card drag starts, and mobile", async ({ page }) => {
    await setup(page);
    for (const options of [{ inside: true }, { dy: 45 }, { dx: -60 }, { dx: 120 }, { cancel: true }, { blocked: true }]) {
        await pullBoard(page, options);
        await expect(page.getByRole("dialog")).toHaveCount(0);
    }
    const viewport = page.locator("[data-board-pane]").first().locator("[id^=board-scrollport-]").first();
    await viewport.evaluate((element) => {
        element.scrollLeft = element.scrollWidth;
    });
    const card = page.getByText("left draggable card", { exact: true }).first();
    await expect(card).toBeVisible();
    const bounds = (await card.boundingBox())!;
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    await page.mouse.down();
    await page.mouse.move(bounds.x - 120, bounds.y + bounds.height / 2, { steps: 12 });
    await page.mouse.up();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    await pullBoard(page);
    await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("left pull still opens composer and boundary gestures preserve a draft", async ({ page }) => {
    let submissions = 0;
    page.on("request", (request) => {
        if (request.method() === "POST" && /\/board\/[^/]+\/card$/.test(new URL(request.url()).pathname)) submissions++;
    });
    await setup(page);
    await pullBoard(page, { edge: "left" });
    const draft = page.getByPlaceholder("Enter a title", { exact: true });
    await expect(draft).toBeVisible();
    await draft.fill("Boundary draft retained");
    await pullBoard(page);
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(draft).toHaveValue("Boundary draft retained");
    await pullBoard(page, { edge: "left" });
    await expect(draft).toHaveValue("Boundary draft retained");
    expect(submissions).toBe(0);
});

test("native explorer board drag distinguishes collection copy from board split", async ({ page }) => {
    const receipt = await setup(page, true);
    const explorer = page.locator("[data-workbench-explorer]");
    if (!(await explorer.isVisible())) await page.getByRole("button", { name: "Explorer", exact: true }).click();
    const source = explorer.getByRole("button", { name: "00000000001 board", exact: true }).first();
    const collection = explorer.getByRole("button", { name: "Fixture collection", exact: true });
    await expect(collection).toBeVisible();
    await source.dragTo(collection);
    await expect(page.locator("[data-board-pane]")).toHaveCount(1);
    await collection.click();
    await expect(explorer.getByRole("treeitem").filter({ hasText: "00000000001 board" })).toHaveCount(1);
    expect(receipt().collectionWrites).toBe(1);
    // Arborist rows retain their internal move behavior and expose the same
    // board reference when the destination is a board pane.
    await expect(page).toHaveURL(/\/board\/left$/);
    await explorer
        .getByRole("treeitem")
        .filter({ hasText: "00000000001 board" })
        .getByRole("button", { name: "00000000001 board", exact: true })
        .dragTo(page.locator("[data-board-pane]").first(), { targetPosition: { x: 200, y: 100 } });
    await expect(page.locator("[data-board-pane]")).toHaveCount(2);
    await expect(page.locator("[data-board-pane]").nth(1).getByText("00000000001 column 0", { exact: true }).first()).toBeVisible();
    await expect(explorer.getByRole("treeitem").filter({ hasText: "00000000001 board" })).toHaveCount(1);
    expect(receipt().collectionWrites).toBe(1);
    expect(receipt().savedCollection.collections[0].board_uids).toEqual(["00000000001"]);
});

test("split pane footers stay above Workbench status with both docks and minimaps", async ({ page }) => {
    await setup(page, false, true);
    const status = page.getByRole("contentinfo", { name: "Workbench status" });
    const assertShellBounds = async () => {
        await expect(status).toBeVisible();
        await expect(status).toHaveCount(1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(page.viewportSize()!.width);
        for (const widget of await page
            .locator("[data-board-pane-footer]:visible, [data-floating-nav-content]:visible, [data-board-minimap]:visible")
            .all()) {
            await expect(widget).toBeVisible();
            await expect
                .poll(async () => {
                    const bounds = await widget.boundingBox();
                    return bounds!.y + bounds!.height - (await status.boundingBox())!.y;
                })
                .toBeLessThanOrEqual(0);
        }
    };
    await assertShellBounds();
    const transfer = await page.evaluateHandle(() => {
        const data = new DataTransfer();
        data.setData("application/x-langboard-board-reference", "right");
        return data;
    });
    await page.locator("[data-board-pane]").first().dispatchEvent("drop", { dataTransfer: transfer });
    const panes = page.locator("[data-board-pane]");
    await expect(page.locator("[data-board-pane-footer]")).toHaveCount(2);
    for (const width of [1440, 1100, 1024]) {
        await page.setViewportSize({ width, height: 900 });
        await expect(page.locator("[data-board-pane]:visible")).toHaveCount(2);
        await expect(page.locator("[data-floating-nav-content]:visible")).toHaveCount(2);
        await expect(page.locator("[data-board-minimap]:visible")).toHaveCount(2);
        await assertShellBounds();
        for (const pane of await panes.all()) {
            const bounds = await pane.boundingBox();
            const footer = pane.locator("[data-board-pane-footer]");
            await expect(footer).toBeVisible();
            const footerBounds = await footer.boundingBox();
            expect(footerBounds!.x).toBeGreaterThanOrEqual(bounds!.x);
            expect(footerBounds!.x + footerBounds!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width);
            await expect(pane.getByRole("button", { name: /Close .* board/, exact: true })).toBeEnabled();
        }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("[data-board-pane]:visible")).toHaveCount(1);
    await expect(page.locator("[data-board-pane-footer]:visible")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /in split view/ })).toHaveCount(0);
    await expect(page.locator("[data-floating-nav-content]:visible")).toHaveCount(1);
    await assertShellBounds();
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.locator("[data-board-pane]:visible")).toHaveCount(2);
    await panes.nth(1).getByRole("button", { name: "Close right board", exact: true }).click();
    await expect(panes).toHaveCount(1);
    await expect(page.locator("[data-board-pane-footer]")).toHaveCount(0);
    await expect(page.locator("[data-floating-nav-content]:visible")).toHaveCount(1);
    await assertShellBounds();
});
