import { expect, test } from "@playwright/test";

for (const width of [320, 1280])
    for (const dark of [false, true]) {
        test(`Explorer unread counts retain row interactions at ${width}px, dark=${dark}`, async ({ page }, testInfo) => {
            const errors: string[] = [];
            page.on("pageerror", (error) => errors.push(error.message));
            await page.setViewportSize({ width, height: 1000 });
            await page.goto("/src/pages/DashboardPage/components/explorer-recovery.fixture.html?unread");
            if (dark) await page.locator("html").evaluate((element) => element.classList.add("dark"));
            const explorer = page.getByRole("navigation", { name: "Explorer", exact: true });
            const row = (index: number) => explorer.getByRole("button", { name: new RegExp(`^Unread board ${index} with`) });
            await expect(row(0)).toBeVisible();
            for (const [index, count, text] of [
                [0, 0, ""],
                [1, 1, ""],
                [2, 2, "2"],
                [3, 99, "99"],
                [4, 100, "99+"],
                [5, 105, "99+"],
                [6, 1, ""],
            ] as const) {
                const indicator = row(index).getByRole("img");
                if (!count) {
                    await expect(indicator).toHaveCount(0);
                    continue;
                }
                await expect(indicator).toHaveText(text);
                await expect(indicator).toHaveAttribute("aria-label", index === 6 ? "Unread changes" : `${count} unread board cards`);
                const badgeBox = await indicator.boundingBox();
                const buttonBox = await row(index).boundingBox();
                const titleBox = await row(index).locator("span.truncate").boundingBox();
                expect(badgeBox && buttonBox && titleBox).toBeTruthy();
                expect(badgeBox!.x).toBeGreaterThanOrEqual(titleBox!.x + titleBox!.width);
                expect(badgeBox!.x + badgeBox!.width).toBeLessThanOrEqual(buttonBox!.x + buttonBox!.width);
                expect(await indicator.evaluate((element) => getComputedStyle(element).pointerEvents)).toBe("none");
            }
            const workload = row(2).locator("..").locator("[aria-label='4 open cards']");
            await expect(workload).toBeVisible();
            await page.screenshot({ path: testInfo.outputPath("explorer-unread.png"), fullPage: true });
            await page.getByRole("button", { name: "Apply live count", exact: true }).click();
            await expect(row(2).getByRole("img")).toHaveAttribute("aria-label", "105 unread board cards");
            await expect(workload).toBeVisible();
            if (width >= 768) {
                const split = row(2)
                    .locator("..")
                    .getByRole("button", { name: /^Open .* in split view$/ });
                await split.focus();
                await split.click();
                await expect(split).toBeDisabled();
                await expect(row(2).getByRole("img")).toHaveText("99+");
            }
            await explorer.getByRole("textbox", { name: "Search projects", exact: true }).fill("Unread board 2");
            await expect(explorer.getByRole("button", { name: /^Unread board/ })).toHaveCount(1);
            await expect(row(2).getByRole("img")).toHaveText("99+");
            await row(2).click();
            await expect(page.getByLabel("Current route", { exact: true })).toHaveText("/board/unread-2");
            await expect(row(2)).toHaveAttribute("aria-current", "page");
            await page.getByRole("button", { name: "Mark board read", exact: true }).click();
            await expect(row(2).getByRole("img")).toHaveCount(0);
            await expect(workload).toBeVisible();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            expect(errors).toEqual([]);
        });
    }

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

test("Explorer independently restores each user's collapse preferences after reload, remount and logout", async ({ page }) => {
    await page.addInitScript(() => {
        if (!localStorage.getItem("user-settings-store")) {
            localStorage.setItem("user-settings-store", JSON.stringify({ explorer_open_cards_collapsed: { "explorer-fixture-user": true } }));
        }
    });
    await page.goto("/src/pages/DashboardPage/components/explorer-recovery.fixture.html?sections");
    const explorer = page.getByRole("navigation", { name: "Explorer", exact: true });
    const header = (name: string) => explorer.getByRole("region", { name, exact: true }).getByRole("heading").getByRole("button");
    const states = async (expected: string[]) => {
        for (const [index, name] of ["Recent cards", "Favorites", "Recent work", "Projects"].entries()) {
            await expect(header(name)).toHaveAttribute("aria-expanded", expected[index]);
        }
    };
    await states(["false", "true", "true", "true"]);
    await header("Favorites").click();
    await header("Recent work").click();
    await states(["false", "false", "false", "true"]);
    await expect
        .poll(() =>
            page.evaluate(
                () => JSON.parse(localStorage.getItem("user-settings-store") ?? "{}").explorer_sections_collapsed?.["explorer-fixture-user"]
            )
        )
        .toEqual({ favorites: true, recent: true });
    await page.reload();
    await states(["false", "false", "false", "true"]);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await states(["true", "true", "true", "true"]);
    await header("Projects").click();
    await states(["true", "true", "true", "false"]);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await states(["false", "false", "false", "true"]);
    // Exercise the real auth store with no current actor, then restore A and B.
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const { default: auth } = await import(path);
        (window as unknown as { explorerActor: unknown }).explorerActor = auth.getState().currentUser;
        auth.setState({ currentUser: null });
    });
    await expect(explorer.getByRole("region", { name: "Recent cards", exact: true })).toHaveCount(0);
    for (const name of ["Favorites", "Recent work"]) await expect(explorer.getByRole("region", { name, exact: true })).toHaveCount(0);
    await expect(explorer.getByRole("button", { name: /^Board \d+$/ })).toHaveCount(0);
    await expect(header("Projects")).toHaveAttribute("aria-expanded", "true");
    await header("Projects").click();
    await expect(header("Projects")).toHaveAttribute("aria-expanded", "true");
    expect(
        await page.evaluate(() => Object.keys(JSON.parse(localStorage.getItem("user-settings-store") ?? "{}").explorer_sections_collapsed))
    ).toEqual(["explorer-fixture-user", "other-explorer-user"]);
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const { default: auth } = await import(path);
        auth.setState({ currentUser: (window as unknown as { explorerActor: unknown }).explorerActor });
    });
    await states(["false", "false", "false", "true"]);
    // Unmount the authenticated subtree, then mount the same actor without reloading.
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const { default: auth } = await import(path);
        auth.setState({ state: "pending" });
    });
    await expect(explorer).toHaveCount(0);
    await page.evaluate(async () => {
        const path = "/src/core/stores/AuthStore.ts";
        const { default: auth } = await import(path);
        auth.setState({ state: "loaded" });
    });
    await states(["false", "false", "false", "true"]);
    await page.reload();
    await states(["false", "false", "false", "true"]);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await states(["true", "true", "true", "false"]);
    await header("Projects").click();
    await states(["true", "true", "true", "true"]);
    await header("Favorites").click();
    await header("Recent work").click();
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await states(["false", "false", "false", "true"]);
});

test("Explorer pins stay above an unpinned card through focus, close and reopen", async ({ page }) => {
    await page.goto("/src/pages/DashboardPage/components/explorer-recovery.fixture.html?sections");
    const cards = page.getByRole("region", { name: "Recent cards", exact: true });
    const titles = () => cards.locator("[data-recent-card-list] > div > button:first-child").allTextContents();
    await cards.getByRole("button", { name: "Show more (4)", exact: true }).click();
    await cards
        .locator("[data-recent-card-list] > div")
        .filter({ has: page.getByRole("button", { name: "Card 13", exact: true }) })
        .getByRole("button", { name: "Pin card", exact: true })
        .click();
    await expect.poll(async () => (await titles())[0]).toBe("Card 13");
    const focusUnpinned = async () =>
        page.evaluate(async () => {
            const path = "/src/pages/DashboardPage/components/OpenCardsStore.ts";
            const { focusCard } = await import(path);
            focusCard("explorer-fixture-user", { projectUID: "fixture-0", cardUID: "0", title: "Card 0" });
        });
    await focusUnpinned();
    await expect.poll(async () => (await titles()).slice(0, 2)).toEqual(["Card 13", "Card 0"]);
    await cards
        .locator("[data-recent-card-list] > div")
        .filter({ has: page.getByRole("button", { name: "Card 0", exact: true }) })
        .getByRole("button", { name: "Close card from list", exact: true })
        .click();
    await expect(cards.getByRole("button", { name: "Card 0", exact: true })).toHaveCount(0);
    await focusUnpinned();
    await expect.poll(async () => (await titles()).slice(0, 2)).toEqual(["Card 13", "Card 0"]);
    await expect(cards.getByRole("button", { name: "Unpin card", exact: true })).toHaveCount(1);
    expect(
        await page.evaluate(async () => {
            const path = "/src/pages/DashboardPage/components/OpenCardsData.ts";
            const settingsPath = "/src/core/stores/UserSettingsStore.ts";
            const { recentOpenCards } = await import(path);
            const { getUserSettingsStore } = await import(settingsPath);
            return recentOpenCards(getUserSettingsStore().settings.open_cards["explorer-fixture-user"])[0].cardUID;
        })
    ).toBe("0");
});
