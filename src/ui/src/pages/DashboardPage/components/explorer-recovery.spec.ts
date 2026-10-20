import { expect, test } from "@playwright/test";

for (const cached of [false, true]) {
    test(`Explorer displays failed ${cached ? "refresh" : "initial load"} and recovers through Retry`, async ({ page }) => {
        await page.goto(`/src/pages/DashboardPage/components/explorer-recovery.fixture.html${cached ? "?cached" : ""}`);
        await expect(page.getByRole("alert")).toContainText("Could not load projects");
        const board = page.getByRole("button", { name: "Retained board", exact: true });
        if (cached) await expect(board).toBeVisible();
        else await expect(board).toHaveCount(0);
        await page.getByRole("button", { name: "Recover service", exact: true }).click();
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.getByRole("alert")).toHaveCount(0);
        await expect(board).toBeVisible();
    });
}

for (const width of [1280, 390]) {
    test(`unified sections bound rows, share one scrollport and persist user order at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/pages/DashboardPage/components/explorer-recovery.fixture.html?sections");
        const explorer = page.getByRole("navigation", { name: "Explorer", exact: true });
        const cards = explorer.getByRole("region", { name: "Recent cards", exact: true });
        const favorites = explorer.getByRole("region", { name: "Favorites", exact: true });
        const projects = explorer.getByRole("region", { name: "Projects", exact: true });
        await expect(cards.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
        await expect(favorites.getByRole("button", { name: /^Board \d+$/ })).toHaveCount(10);
        await expect(projects.getByRole("button", { name: /^Board \d+$/ })).toHaveCount(10);
        await cards.getByRole("button", { name: "Show more (4)", exact: true }).click();
        await expect(cards.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(14);
        await cards.getByRole("button", { name: "Show fewer", exact: true }).click();
        await favorites.getByRole("button", { name: "Show more (2)", exact: true }).click();
        await expect(favorites.getByRole("button", { name: /^Board \d+$/ })).toHaveCount(12);
        await favorites.getByRole("button", { name: "Show fewer", exact: true }).click();
        const sectionTitles = explorer.locator("[data-explorer-section-list] > section > h2");
        const order = () => sectionTitles.allTextContents().then((titles) => titles.map((title) => title.replace(/\d+$/, "")));
        await expect.poll(order).toEqual(["Recent cards", "Favorites", "Recent work", "Projects"]);
        const projectsTitle = projects.getByRole("heading").getByRole("button");
        await projectsTitle.press("Alt+ArrowUp");
        await expect.poll(order).toEqual(["Recent cards", "Favorites", "Projects", "Recent work"]);
        const cardsTitle = cards.getByRole("heading").getByRole("button");
        await cardsTitle.click();
        await favorites.getByRole("heading").getByRole("button").click();
        await projectsTitle.click();
        await projectsTitle.dragTo(cardsTitle);
        await expect.poll(order).toEqual(["Projects", "Recent cards", "Favorites", "Recent work"]);
        await page.reload();
        await expect.poll(order).toEqual(["Projects", "Recent cards", "Favorites", "Recent work"]);
        await page.getByRole("button", { name: "Switch account", exact: true }).click();
        await expect.poll(order).toEqual(["Recent cards", "Favorites", "Recent work", "Projects"]);
        await page.getByRole("button", { name: "Switch account", exact: true }).click();
        await expect.poll(order).toEqual(["Projects", "Recent cards", "Favorites", "Recent work"]);
        const scrollports = await explorer.evaluate(
            (root) => [...root.querySelectorAll("*")].filter((node) => ["auto", "scroll"].includes(getComputedStyle(node).overflowY)).length
        );
        expect(scrollports).toBe(1);
        await explorer.getByRole("textbox", { name: "Search projects", exact: true }).fill("Board");
        await expect(explorer.getByRole("region")).toHaveCount(1);
        await expect(projects.getByRole("button", { name: /^Board \d+$/ })).toHaveCount(10);
        await projects.getByRole("button", { name: "Show more (26)", exact: true }).click();
        await expect(projects.getByRole("button", { name: /^Board \d+$/ })).toHaveCount(36);
        expect(await page.locator("button button").count()).toBe(0);
    });
}
