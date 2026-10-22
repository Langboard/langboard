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
});
