import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page) {
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
    const projects = ["left", "right", "third"].map((uid) => ({
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
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user, bots: [] };
            else if (path === `/board/${uid}`)
                json = { project: projects.find((project) => project.uid === uid), project_bot_scopes: [], project_bot_schedules: [] };
            else if (path === `/board/${uid}/cards`)
                json = {
                    cards: [],
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
}

test("explorer opens two real boards with one shell; either close preserves survivor DOM", async ({ page }) => {
    await setup(page);
    if (!(await page.locator("[data-workbench-explorer]").isVisible())) await page.getByRole("button", { name: "Explorer", exact: true }).click();
    const split = page.getByRole("button", { name: "Open right board in split view" }).first();
    await split.hover();
    await split.click({ force: true });
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
