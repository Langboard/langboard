import { expect, test } from "@playwright/test";
const first = "00000000-0000-4000-8000-000000000001";
const second = "00000000-0000-4000-8000-000000000002";
for (const width of [1280, 390]) {
    test(`collection tree persists board drop, order and favorites with editor at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 950 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        let saved = {
            revision: 1,
            collections: [
                { uid: first, name: "First collection", board_uids: ["00000000001", "00000000002"] },
                { uid: second, name: "Second collection", board_uids: [] as string[] },
            ],
        };
        let puts = 0;
        let stars = 0;
        await page.route("**/account/project-collections", (route) => {
            if (route.request().method() === "GET") return route.fulfill({ json: saved });
            puts++;
            const input = route.request().postDataJSON();
            expect(input.expected_revision).toBe(saved.revision);
            saved = { revision: saved.revision + 1, collections: input.collections };
            return route.fulfill({ json: saved });
        });
        await page.route("**/dashboard/projects/*/star", (route) => {
            stars++;
            return route.fulfill({ json: {} });
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html");
        const output = page.getByTestId("collections");
        await expect(output).toHaveAttribute("data-busy", "false");
        await page.getByRole("button", { name: "First collection", exact: true }).focus();
        await page.keyboard.press("Enter");
        await expect(page.getByRole("button", { name: "Zulu", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Collection actions", exact: true }).first().click();
        await page.getByRole("button", { name: "Sort boards by name", exact: true }).click();
        await expect.poll(() => saved.collections[0].board_uids).toEqual(["00000000002", "00000000001"]);
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Alpha", exact: true }).dragTo(page.getByRole("button", { name: "Zulu", exact: true }), {
            targetPosition: { x: 50, y: 31 },
        });
        await expect.poll(() => saved.collections[0].board_uids).toEqual(["00000000001", "00000000002"]);
        await page.getByRole("button", { name: "Alpha", exact: true }).click();
        await expect(page.getByTestId("destination")).toHaveText("00000000002");
        const favorite = page.getByRole("button", { name: "Favorites", exact: true }).first();
        await favorite.click();
        await expect(favorite).toHaveAttribute("aria-pressed", "true");
        expect(stars).toBe(1);
        const source = page.locator("button[draggable=true]").first();
        const target = page.getByRole("button", { name: "Second collection", exact: true });
        await source.dragTo(target);
        await expect.poll(() => saved.collections[1].board_uids).toEqual(["00000000003"]);
        const before = puts;
        await source.dragTo(target);
        await expect(output).toHaveAttribute("data-busy", "false");
        expect(puts).toBe(before);
        await page.getByRole("button", { name: "Alpha", exact: true }).dragTo(target);
        await expect.poll(() => saved.collections[0].board_uids).toEqual(["00000000001"]);
        await expect.poll(() => saved.collections[1].board_uids).toContain("00000000002");
        await page.getByRole("button", { name: "Collection actions", exact: true }).last().click();
        await page.getByRole("button", { name: "Move collection up", exact: true }).click();
        await expect.poll(() => saved.collections[0].uid).toBe(second);
        await page.keyboard.press("Escape");
        await page.reload();
        await expect(output).toContainText("Second collection");
        await page
            .getByRole("button", { name: "Second collection", exact: true })
            .dragTo(page.getByRole("button", { name: "First collection", exact: true }), { targetPosition: { x: 50, y: 31 } });
        await expect.poll(() => saved.collections[0].uid).toBe(first);
        await page.getByRole("button", { name: "First collection", exact: true }).click();
        await page.getByRole("button", { name: "Second collection", exact: true }).click();
        await expect(page.locator("[contenteditable=true]").first()).toBeVisible();
        if (width === 390) await page.screenshot({ path: "/tmp/langboard-project-collection-tree-390.png", fullPage: true });
        expect(errors).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("maximum collection tree renders bounded rows and hides unreadable board titles", async ({ page }) => {
    const collections = Array.from({ length: 64 }, (_, index) => ({
        uid: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
        name: `Group ${index}`,
        board_uids: Array.from({ length: 16 }, (_, child) => String(index * 16 + child + 100).padStart(11, "0")),
    }));
    await page.route("**/account/project-collections", (route) => route.fulfill({ json: { revision: 1, collections } }));
    await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html");
    await expect(page.getByRole("button", { name: "Group 0", exact: true })).toBeVisible();
    expect(await page.getByRole("treeitem").count()).toBeLessThan(25);
    await page.getByRole("button", { name: "Group 0", exact: true }).click();
    await expect(page.getByRole("button", { name: "Board unavailable", exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Board unavailable", exact: true }).first().click();
    await expect(page.getByTestId("destination")).toHaveText("");
    expect(await page.getByRole("treeitem").count()).toBeLessThan(25);
    await page.getByRole("tree").focus();
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("*");
    await page.keyboard.press("End");
    const lastBoard = page.getByRole("treeitem").last();
    await expect(lastBoard).toBeFocused();
    await expect(lastBoard).toHaveAttribute("aria-level", "2");
    await expect(lastBoard.locator("[data-collection-uid]")).toHaveAttribute("data-collection-uid", collections[63].uid);
    expect(await page.getByRole("treeitem").count()).toBeLessThan(25);
    await page.keyboard.press("Home");
    await expect(page.getByRole("button", { name: "Group 0", exact: true })).toBeVisible();
});

test("focused collection controls retain native arrow navigation and board activation", async ({ page }) => {
    await page.route("**/account/project-collections", (route) =>
        route.fulfill({
            json: {
                revision: 1,
                collections: [{ uid: first, name: "Keyboard collection", board_uids: ["00000000001"] }],
            },
        })
    );
    await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html");
    await page.getByRole("button", { name: "Keyboard collection", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("button", { name: "Zulu", exact: true })).toBeVisible();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("treeitem").filter({ has: page.getByRole("button", { name: "Zulu", exact: true }) })).toBeFocused();
    await page.keyboard.press("Space");
    await expect(page.getByTestId("destination")).toHaveText("00000000001");
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    await expect(page.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(0);
});

test("revoked board access removes titles and favorites and blocks activation", async ({ page }) => {
    let writes = 0;
    await page.route("**/account/project-collections", (route) => {
        if (route.request().method() !== "GET") writes++;
        return route.fulfill({ json: { revision: 1, collections: [{ uid: first, name: "Permission collection", board_uids: ["00000000001"] }] } });
    });
    await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html");
    await page.getByRole("button", { name: "Permission collection", exact: true }).click();
    await expect(page.getByRole("button", { name: "Zulu", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Remove board access", exact: true }).click();
    await expect(page.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(0);
    await expect(page.getByRole("tree").getByRole("button", { name: "Favorites", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Board unavailable", exact: true }).click();
    await expect(page.getByTestId("destination")).toHaveText("");
    await page.getByRole("tree").focus();
    await page.keyboard.press("End");
    await page.keyboard.press("Space");
    await expect(page.getByTestId("destination")).toHaveText("");
    expect(writes).toBe(0);
});

test.describe("touch collection controls", () => {
    test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
    test("tap expands, sorts, favorites and opens a linked board", async ({ page }, testInfo) => {
        let saved = { revision: 1, collections: [{ uid: first, name: "Touch collection", board_uids: ["00000000001", "00000000002"] }] };
        let stars = 0;
        await page.route("**/account/project-collections", (route) => {
            if (route.request().method() === "PUT") saved = { revision: saved.revision + 1, collections: route.request().postDataJSON().collections };
            return route.fulfill({ json: saved });
        });
        await page.route("**/dashboard/projects/*/star", (route) => {
            stars++;
            return route.fulfill({ json: {} });
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html");
        await page.getByRole("button", { name: "Touch collection", exact: true }).tap();
        await expect(page.getByRole("button", { name: "Zulu", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Collection actions", exact: true }).tap();
        await page.getByRole("button", { name: "Sort boards by name", exact: true }).tap();
        await expect.poll(() => saved.collections[0].board_uids).toEqual(["00000000002", "00000000001"]);
        await page.keyboard.press("Escape");
        const favorite = page.getByRole("tree").getByRole("button", { name: "Favorites", exact: true }).first();
        await favorite.tap();
        await expect(favorite).toHaveAttribute("aria-pressed", "true");
        expect(stars).toBe(1);
        await expect(page.getByTestId("destination")).toHaveText("");
        await page.getByRole("button", { name: "Alpha", exact: true }).tap();
        await expect(page.getByTestId("destination")).toHaveText("00000000002");
        expect(await page.evaluate(() => navigator.maxTouchPoints > 0 && document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath("touch-390.png"), fullPage: true });
    });
});

for (const empty of [false, true]) {
    test(`production Explorer honors completed ${empty ? "empty" : "partial"} permission refresh over cached current board`, async ({ page }) => {
        let pending: import("@playwright/test").Route | undefined;
        let requests = 0;
        let writes = 0;
        await page.route("**/account/project-collections", (route) => {
            if (route.request().method() !== "GET") writes++;
            return route.fulfill({
                json: { revision: 1, collections: [{ uid: first, name: "Permission collection", board_uids: ["00000000001"] }] },
            });
        });
        const board = (uid: string, title: string) => ({
            uid,
            title,
            starred: true,
            project_type: "Other",
            owner_uid: "collection-tree",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            all_members: [],
            current_auth_role_actions: [],
        });
        await page.route("**/dashboard/projects", (route) => {
            requests++;
            if (requests === 1) {
                pending = route;
                return;
            }
            return route.fulfill({ json: { projects: empty ? [] : [board("00000000002", "Alpha")], columns: [] } });
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html?explorer");
        const explorer = page.getByRole("navigation", { name: "Explorer", exact: true });
        const tree = explorer.getByRole("tree");
        const header = explorer.getByRole("region", { name: "Zulu", exact: true });
        await expect(explorer.getByRole("status")).toBeVisible();
        await expect(header.getByRole("button", { name: "Board", exact: true })).toHaveAttribute("aria-current", "page");
        await explorer.getByRole("button", { name: "Permission collection", exact: true }).click();
        await expect(tree.getByRole("button", { name: "Zulu", exact: true })).toBeVisible();
        await explorer.getByRole("button", { name: "Add project collection", exact: true }).click();
        await expect(page.getByRole("dialog").getByRole("checkbox", { name: "Zulu", exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await expect.poll(() => !!pending).toBe(true);
        await pending!.fulfill({ json: { projects: [board("00000000001", "Zulu"), board("00000000002", "Alpha")], columns: [] } });
        await expect(explorer.getByRole("status")).toHaveCount(0);
        await expect(
            explorer.getByRole("region", { name: "Favorites", exact: true }).getByRole("button", { name: "Zulu", exact: true })
        ).toBeVisible();
        await page.getByRole("button", { name: "Refresh board permissions", exact: true }).click();
        await expect.poll(() => requests).toBe(2);
        await expect(tree.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(0);
        await expect(tree.getByRole("button", { name: "Favorites", exact: true })).toHaveCount(0);
        await tree.getByRole("button", { name: "Board unavailable", exact: true }).click();
        await expect(header.getByRole("button", { name: "Board", exact: true })).toHaveAttribute("aria-current", "page");
        await expect(explorer.locator("button[draggable=true]").filter({ hasText: "Zulu" })).toHaveCount(0);
        if (!empty) await expect(explorer.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
        await explorer.getByRole("button", { name: "Add project collection", exact: true }).click();
        await expect(page.getByRole("dialog").getByRole("checkbox", { name: "Zulu", exact: true })).toHaveCount(0);
        if (!empty) await expect(page.getByRole("dialog").getByRole("checkbox", { name: "Alpha", exact: true })).toBeVisible();
        expect(writes).toBe(0);
    });
}

for (const touch of [false, true]) {
    test.describe(`${touch ? "touch" : "keyboard"} board ordering menus`, () => {
        test.use({ hasTouch: touch, viewport: { width: touch ? 390 : 1280, height: 950 } });
        test("move references without dragging, reject boundaries and duplicates, and lock pending saves", async ({ page }) => {
            let saved = {
                revision: 1,
                collections: [
                    { uid: first, name: "First collection", board_uids: ["00000000001", "00000000002", "00000000003"] },
                    { uid: second, name: "Second collection", board_uids: ["00000000001"] },
                ],
            };
            let pending: import("@playwright/test").Route | undefined;
            let writes = 0;
            await page.route("**/account/project-collections", (route) => {
                if (route.request().method() === "GET") return route.fulfill({ json: saved });
                writes++;
                pending = route;
            });
            const invoke = async (button: import("@playwright/test").Locator) => {
                if (touch) await button.tap();
                else {
                    await button.focus();
                    await page.keyboard.press("Enter");
                }
            };
            const complete = async (expected: string[][]) => {
                await expect.poll(() => !!pending).toBe(true);
                const route = pending!;
                pending = undefined;
                const input = route.request().postDataJSON();
                expect(input.expected_revision).toBe(saved.revision);
                expect(input.collections.map((item: { board_uids: string[] }) => item.board_uids)).toEqual(expected);
                saved = { revision: saved.revision + 1, collections: input.collections };
                await route.fulfill({ json: saved });
                await expect(page.getByTestId("collections")).toHaveText(JSON.stringify(saved.collections));
                await expect(page.getByTestId("collections")).toHaveAttribute("data-busy", "false");
            };
            await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html");
            await invoke(page.getByRole("button", { name: "First collection", exact: true }));
            const row = (title: string) => page.getByRole("treeitem").filter({ has: page.getByRole("button", { name: title, exact: true }) });
            const actions = (title: string) => row(title).getByRole("button", { name: "Board actions", exact: true });
            const up = page.getByRole("button", { name: "Move board up", exact: true });
            const down = page.getByRole("button", { name: "Move board down", exact: true });
            const target = page.getByRole("button", { name: "Move to collection: Second collection", exact: true });
            if (touch) await invoke(actions("Zulu"));
            else {
                await row("Zulu").getByRole("button", { name: "Zulu", exact: true }).focus();
                await page.keyboard.press("Tab");
                await page.keyboard.press("Tab");
                await expect(actions("Zulu")).toBeFocused();
                await page.keyboard.press("Enter");
            }
            await expect(up).toBeDisabled();
            await expect(target).toBeDisabled();
            await expect(down).toBeEnabled();
            await page.keyboard.press("Escape");
            await expect(page.getByRole("dialog")).toHaveCount(0);
            await invoke(actions("New"));
            await expect(down).toBeDisabled();
            await page.keyboard.press("Escape");
            await expect(page.getByRole("dialog")).toHaveCount(0);
            expect(writes).toBe(0);
            await invoke(actions("Alpha"));
            await invoke(down);
            await expect(page.getByTestId("collections")).toHaveAttribute("data-busy", "true");
            await expect(page.getByRole("dialog")).toHaveCount(0);
            await expect(actions("Zulu")).toBeDisabled();
            await expect(actions("Alpha")).toBeDisabled();
            await expect(actions("New")).toBeDisabled();
            await complete([["00000000001", "00000000003", "00000000002"], ["00000000001"]]);
            await invoke(actions("Alpha"));
            await invoke(up);
            await complete([["00000000001", "00000000002", "00000000003"], ["00000000001"]]);
            await invoke(actions("Alpha"));
            await invoke(up);
            await complete([["00000000002", "00000000001", "00000000003"], ["00000000001"]]);
            await invoke(actions("Alpha"));
            await expect(up).toBeDisabled();
            if (touch) await page.screenshot({ path: "/private/tmp/board-menu-touch-390.png", fullPage: true, animations: "disabled" });
            await invoke(target);
            await complete([
                ["00000000001", "00000000003"],
                ["00000000001", "00000000002"],
            ]);
            await page.keyboard.press("Escape");
            await expect(page.getByRole("dialog")).toHaveCount(0);
            await expect(page.getByTestId("destination")).toHaveText("");
            expect(writes).toBe(4);
            await page.reload();
            await invoke(page.getByRole("button", { name: "Second collection", exact: true }));
            await expect(row("Alpha")).toBeVisible();
            await invoke(page.getByRole("button", { name: "Remove board access", exact: true }));
            await expect(page.getByRole("tree").getByRole("button", { name: "Board actions", exact: true })).toHaveCount(0);
            await expect(page.getByRole("tree").getByRole("button", { name: "Favorites", exact: true })).toHaveCount(0);
            expect(writes).toBe(4);
            await expect(page.getByTestId("destination")).toHaveText("");
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        });
    });
}

for (const touch of [false, true]) {
    test.describe(`${touch ? "touch" : "desktop"} collection favorites in production Explorer`, () => {
        test.use({ hasTouch: touch, viewport: { width: touch ? 390 : 1280, height: 950 } });
        test("collection and source-board favorites persist independently and appear once", async ({ page }) => {
            let saved = {
                revision: 4,
                collections: [
                    { uid: first, name: "Hidden collection", board_uids: [] as string[], starred: false },
                    { uid: second, name: "Favorite collection group", board_uids: ["00000000001"], starred: false },
                ],
            };
            let boardStarred = false;
            let collectionWrites = 0;
            let boardWrites = 0;
            await page.route("**/account/project-collections", (route) => {
                if (route.request().method() === "PUT") {
                    const input = route.request().postDataJSON();
                    expect(input.expected_revision).toBe(saved.revision);
                    expect(input.collections.map((item: { uid: string }) => item.uid)).toEqual([first, second]);
                    saved = { revision: saved.revision + 1, collections: input.collections };
                    collectionWrites++;
                }
                return route.fulfill({ json: saved });
            });
            await page.route("**/dashboard/projects", (route) =>
                route.fulfill({
                    json: {
                        projects: [
                            {
                                uid: "00000000001",
                                title: "Zulu",
                                starred: boardStarred,
                                project_type: "Other",
                                owner_uid: "collection-tree",
                                created_at: new Date().toISOString(),
                                updated_at: new Date().toISOString(),
                                all_members: [],
                                current_auth_role_actions: [],
                            },
                        ],
                        columns: [],
                    },
                })
            );
            await page.route("**/dashboard/projects/*/star", (route) => {
                boardStarred = !boardStarred;
                boardWrites++;
                return route.fulfill({ json: {} });
            });
            const invoke = async (button: import("@playwright/test").Locator) => {
                if (touch) await button.tap();
                else {
                    await button.focus();
                    await page.keyboard.press("Enter");
                }
            };
            await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html?explorer");
            const explorer = page.getByRole("navigation", { name: "Explorer", exact: true });
            const projects = explorer.getByRole("region", { name: "Projects", exact: true });
            const favorites = explorer.getByRole("region", { name: "Favorites", exact: true });
            const row = (section: import("@playwright/test").Locator) =>
                section.getByRole("treeitem").filter({
                    has: page.getByRole("button", { name: "Favorite collection group", exact: true }),
                });
            await expect(projects.getByRole("button", { name: "Favorite collection group", exact: true })).toBeVisible();
            await expect(favorites).toHaveCount(0);
            await invoke(row(projects).getByRole("button", { name: "Favorite collection", exact: true }));
            await expect(row(favorites)).toHaveCount(1);
            await expect(row(favorites).getByRole("button", { name: "Favorite collection", exact: true })).toHaveAttribute("aria-pressed", "true");
            expect(boardWrites).toBe(0);
            expect(saved.collections[0].starred).toBe(false);
            await expect(favorites.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(0);
            await invoke(projects.getByRole("button", { name: "Favorite collection group", exact: true }));
            const boardFavorite = projects.getByRole("tree").getByRole("button", { name: "Favorites", exact: true });
            await invoke(boardFavorite);
            await expect(boardFavorite).toHaveAttribute("aria-pressed", "true");
            await expect(favorites.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(1);
            expect(collectionWrites).toBe(1);
            expect(saved.collections[1].starred).toBe(true);
            await page.reload();
            await expect(row(favorites)).toHaveCount(1);
            await expect(favorites.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(1);
            await invoke(row(favorites).getByRole("button", { name: "Favorite collection", exact: true }));
            await expect(row(favorites)).toHaveCount(0);
            await expect(favorites.getByRole("button", { name: "Zulu", exact: true })).toHaveCount(1);
            expect(boardWrites).toBe(1);
            await invoke(projects.getByRole("button", { name: "Favorite collection group", exact: true }));
            await invoke(boardFavorite);
            await expect(favorites).toHaveCount(0);
            expect(collectionWrites).toBe(2);
            expect(saved.collections[1].board_uids).toEqual(["00000000001"]);
            await page.reload();
            await expect(projects.getByRole("button", { name: "Favorite collection group", exact: true })).toBeVisible();
            await expect(favorites).toHaveCount(0);
        });
    });
}

test("production Explorer moves boards between Favorites and Projects trees atomically", async ({ page }) => {
    let saved = {
        revision: 1,
        collections: [
            { uid: first, name: "Favorite group", starred: true, board_uids: ["00000000001", "00000000002"] },
            { uid: second, name: "Project group", starred: false, board_uids: ["00000000002"] },
        ],
    };
    let writes = 0;
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/account/project-collections", (route) => {
        if (route.request().method() === "GET") return route.fulfill({ json: saved });
        const input = route.request().postDataJSON();
        expect(input.expected_revision).toBe(saved.revision);
        writes++;
        saved = { revision: saved.revision + 1, collections: input.collections };
        return route.fulfill({ json: saved });
    });
    await page.route("**/dashboard/projects", (route) =>
        route.fulfill({
            json: {
                projects: ["Zulu", "Alpha"].map((title, index) => ({
                    uid: `0000000000${index + 1}`,
                    title,
                    starred: false,
                    project_type: "Other",
                    owner_uid: "collection-tree",
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString(),
                    all_members: [],
                    current_auth_role_actions: [],
                })),
                columns: [],
            },
        })
    );
    await page.goto("/src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html?explorer");
    const explorer = page.getByRole("navigation", { name: "Explorer", exact: true });
    const favorites = explorer.getByRole("region", { name: "Favorites", exact: true });
    const projects = explorer.getByRole("region", { name: "Projects", exact: true });
    const button = (section: import("@playwright/test").Locator, name: string) => section.getByRole("button", { name, exact: true });
    await button(favorites, "Favorite group").click();
    await button(projects, "Favorite group").click();
    await button(projects, "Project group").click();
    await button(favorites, "Zulu").dragTo(button(projects, "Project group"));
    await expect.poll(() => saved.collections.map((item) => item.board_uids)).toEqual([["00000000002"], ["00000000002", "00000000001"]]);
    expect(writes).toBe(1);
    await projects.getByRole("tree").getByRole("button", { name: "Zulu", exact: true }).dragTo(button(favorites, "Favorite group"));
    await expect.poll(() => saved.collections.map((item) => item.board_uids)).toEqual([["00000000002", "00000000001"], ["00000000002"]]);
    expect(writes).toBe(2);
    await button(favorites, "Alpha").dragTo(button(projects, "Project group"));
    await expect(button(favorites, "Alpha")).toBeVisible();
    expect(writes).toBe(2);
    await page.reload();
    await button(favorites, "Favorite group").click();
    await expect(button(favorites, "Zulu")).toBeVisible();
    expect(saved.revision).toBe(3);
    expect(errors).toEqual([]);
});
