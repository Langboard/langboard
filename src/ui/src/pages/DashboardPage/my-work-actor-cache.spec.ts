import { expect, test, type Page } from "@playwright/test";
test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => console.log("Fixture browser error:", error.message));
});
const fixture = "/src/pages/DashboardPage/my-work-actor-cache.fixture.html";
const work = (page: Page) => page.getByRole("region", { name: "My Work", exact: true });
const requests = (page: Page) =>
    page.evaluate(
        () =>
            (window as unknown as { myWorkFixture: { requests: { actor?: string; project: string | null; aborted: boolean; completed: boolean }[] } })
                .myWorkFixture.requests
    );

test("cached fast switch never shows prior actor cards", async ({ page }) => {
    await page.goto(fixture);
    await expect(work(page).getByText("actor-A private work")).toBeVisible();
    await page.evaluate(() => {
        const leaks: string[] = [];
        Object.assign(window, { myWorkActorLeaks: leaks });
        new MutationObserver(() => {
            if (
                document.body.textContent?.includes("Actor: actor-B") &&
                document.querySelector("[aria-label='My Work']")?.textContent?.includes("actor-A private work")
            )
                leaks.push("Previous actor card rendered after actor switch");
        }).observe(document.body, { childList: true, subtree: true, characterData: true });
    });
    await page.getByRole("button", { name: "Switch actor", exact: true }).click();
    await expect(page.getByText("Actor: actor-B", { exact: true })).toBeVisible();
    await expect(work(page).getByText("actor-A private work")).toHaveCount(0);
    await expect(work(page).getByText("actor-B private work")).toBeVisible();
    expect((await requests(page)).map((request) => request.actor)).toEqual(["actor-A", "actor-B"]);
    expect(await page.evaluate(() => (window as unknown as { myWorkActorLeaks: string[] }).myWorkActorLeaks)).toEqual([]);
});

test("delayed prior actor response is aborted and cannot enter cache", async ({ page }) => {
    await page.goto(`${fixture}?delayed`);
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    await page.getByRole("button", { name: "Switch actor", exact: true }).click();
    await expect(work(page).getByText("actor-B private work")).toBeVisible();
    await page.getByRole("button", { name: "Release previous", exact: true }).click();
    await expect.poll(async () => (await requests(page))[0].completed).toBe(true);
    expect((await requests(page))[0].aborted).toBe(true);
    await expect(work(page).getByText("actor-A private work")).toHaveCount(0);
    const cache = await page.evaluate(() => (window as unknown as { myWorkFixture: { cache: () => unknown } }).myWorkFixture.cache());
    expect(JSON.stringify(cache)).not.toContain("actor-A private work");
});

test("current actor fence rejects a retained nonaborted previous query", async ({ page }) => {
    await page.goto(`${fixture}?delayed&retain`);
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    await page.getByRole("button", { name: "Switch actor", exact: true }).click();
    await expect(work(page).getByText("actor-B private work")).toBeVisible();
    await page.getByRole("button", { name: "Release previous", exact: true }).click();
    await expect.poll(async () => (await requests(page))[0].completed).toBe(true);
    expect((await requests(page))[0].aborted).toBe(false);
    await expect
        .poll(async () =>
            JSON.stringify(await page.evaluate(() => (window as unknown as { myWorkFixture: { cache: () => unknown } }).myWorkFixture.cache()))
        )
        .toContain("My Work request no longer belongs to the current actor");
    await expect(work(page).getByText("actor-A private work")).toHaveCount(0);
});

test("logout performs no work query, including remount", async ({ page }) => {
    await page.goto(fixture);
    await expect(work(page).getByText("actor-A private work")).toBeVisible();
    await page.getByRole("button", { name: "Logout", exact: true }).click();
    await expect(page.getByText("Actor: signed out", { exact: true })).toBeVisible();
    await expect(work(page).getByText("actor-A private work")).toHaveCount(0);
    await page.getByRole("button", { name: "Toggle work", exact: true }).click();
    await expect(work(page)).toHaveCount(0);
    await page.getByRole("button", { name: "Toggle work", exact: true }).click();
    await expect(work(page)).toBeVisible();
    expect(await requests(page)).toHaveLength(1);
});

test("same UID token refresh retains fresh cache without another fetch", async ({ page }) => {
    await page.goto(fixture);
    await expect(work(page).getByText("actor-A private work")).toBeVisible();
    await page.getByRole("button", { name: "Refresh token", exact: true }).click();
    await expect(page.getByText("Token refresh complete", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Toggle work", exact: true }).click();
    await expect(work(page)).toHaveCount(0);
    await page.getByRole("button", { name: "Toggle work", exact: true }).click();
    await expect(work(page).getByText("actor-A private work")).toBeVisible();
    expect(await requests(page)).toHaveLength(1);
});

for (const compact of [false, true]) {
    test(`${compact ? "panel" : "page"} project filters and responsive controls`, async ({ page }) => {
        await page.goto(`${fixture}${compact ? "?compact" : ""}`);
        await expect(work(page).getByText("actor-A private work")).toBeVisible();
        expect((await requests(page))[0].project).toBe("project-one");
        await work(page).getByRole("button", { name: "All projects", exact: true }).click();
        await expect.poll(async () => (await requests(page)).length).toBe(2);
        expect((await requests(page))[1].project).toBeNull();
        await work(page).getByRole("button", { name: "Current project", exact: true }).click();
        await expect(work(page).getByRole("button", { name: "Current project", exact: true })).toHaveAttribute("aria-pressed", "true");
        await work(page).getByRole("combobox").selectOption("project-two");
        await expect.poll(async () => (await requests(page)).length).toBe(3);
        expect((await requests(page))[2].project).toBe("project-two");
        for (const width of [390, 1440]) {
            await page.setViewportSize({ width, height: 844 });
            await expect(work(page).getByRole("combobox")).toBeVisible();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await page.screenshot({ path: `.verification/my-work-actor-cache-20261019/${compact ? "panel" : "page"}-${width}.png` });
        }
    });
}
