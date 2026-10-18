import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function openFixture(page: Page, mode: string, extra = "") {
    await page.addInitScript(() => localStorage.setItem("lang", "en"));
    await page.route("**/loading-surfaces-test.html*", (route) =>
        route.fulfill({
            contentType: "text/html",
            body: `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1" /></head>
                <body><div id="root"></div><script type="module">
                import RefreshRuntime from "/@react-refresh";
                RefreshRuntime.injectIntoGlobalHook(window);
                window.$RefreshReg$ = () => {};
                window.$RefreshSig$ = () => (type) => type;
                window.__vite_plugin_react_preamble_installed__ = true;
                </script><script type="module" src="/src/pages/BoardPage/components/card/loading-surfaces.fixture.tsx"></script></body></html>`,
        })
    );
    await page.goto(`/loading-surfaces-test.html?mode=${mode}${extra}`);
}
async function control(page: Page, name: string) {
    await page.getByRole("button", { name, exact: true }).evaluate((button) => (button as HTMLButtonElement).click());
}

test("board geometry and DOM survive delay and parent render", async ({ page }) => {
    await openFixture(page, "board");
    const loading = page.locator("[data-board-loading]");
    await expect(loading.getByRole("status")).toHaveCount(1);
    await expect(loading.locator(".animate-pulse").first()).toBeVisible();
    await loading.evaluate((node) => {
        (window as unknown as { originalSkeleton: Element | null }).originalSkeleton = node.querySelector(".grid .animate-pulse");
    });
    const before = await loading.locator(".animate-pulse").count();
    await page.waitForTimeout(2200);
    await control(page, "Render again");
    await expect(loading.locator(".animate-pulse")).toHaveCount(before);
    expect(
        await loading.evaluate(
            (node) => node.querySelector(".grid .animate-pulse") === (window as unknown as { originalSkeleton: Element | null }).originalSkeleton
        )
    ).toBe(true);
});

test("slow load settles, explicit retry settles, cached refresh preserves content", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await openFixture(page, "slow");
    await expect(page.locator("[data-card-loading]")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveAccessibleName("Loading...");
    await expect(page.locator("[data-card-loading]").getByRole("status")).toHaveCount(1);
    await expect(page.locator("output")).toHaveText("1");
    await control(page, "Reject load");
    await expect(page.getByRole("alert")).toContainText("Could not load card");
    await expect(page.locator("[data-card-loading]")).toHaveCount(0);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.locator("output")).toHaveText("2");
    await control(page, "Resolve load");
    await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeVisible();
    await expect(page.locator("[data-card-loading]")).toHaveCount(0);
    await control(page, "Refresh cache");
    await expect(page.locator("output")).toHaveText("3");
    await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeVisible();
    await expect(page.locator("[data-card-loading]")).toHaveCount(0);
    await control(page, "Reject load");
    await expect(page.getByRole("alert")).toContainText("Could not refresh card");
    await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});

for (const mode of ["fast", "cache"])
    test(`${mode} response shows current execution shell`, async ({ page }) => {
        await openFixture(page, mode);
        await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeVisible();
        await expect(page.locator("[data-card-loading]")).toHaveCount(0);
    });

for (const width of [320, 390, 1440])
    for (const dark of [false, true])
        test(`loading visual ${width} ${dark ? "dark" : "light"}`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            await page.emulateMedia({ reducedMotion: "reduce", colorScheme: dark ? "dark" : "light" });
            await openFixture(page, "slow");
            await page.evaluate((value) => document.documentElement.classList.toggle("dark", value), dark);
            const loading = page.locator("[data-card-loading]");
            await expect(loading).toBeVisible();
            expect(
                await loading.locator(".animate-pulse").evaluateAll((nodes) => nodes.every((node) => getComputedStyle(node).animationName === "none"))
            ).toBe(true);
            await expect
                .poll(() => page.getByRole("dialog").evaluate((node) => getComputedStyle(node.parentElement!).transform))
                .toMatch(/^(none|matrix\(1, 0, 0, 1, 0, 0\))$/);
            const box = await page.locator("[data-card-surface]").boundingBox();
            expect(box!.x).toBeGreaterThanOrEqual(0);
            expect(box!.x + box!.width).toBeLessThanOrEqual(width);
            await page.screenshot({ path: `test-results/loading-surfaces/card-${width}-${dark ? "dark" : "light"}.png` });
            await control(page, "Resolve load");
            await expect(page.getByRole("tab", { name: "Execution", exact: true })).toBeVisible();
            const loaded = await page.locator("[data-card-surface]").boundingBox();
            expect(Math.abs(loaded!.x - box!.x)).toBeLessThanOrEqual(1);
            expect(Math.abs(loaded!.width - box!.width)).toBeLessThanOrEqual(1);
            expect(Math.abs(loaded!.y - box!.y)).toBeLessThanOrEqual(1);
            expect(Math.abs(loaded!.height - box!.height)).toBeLessThanOrEqual(1);
            await page.screenshot({ path: `test-results/loading-surfaces/loaded-${width}-${dark ? "dark" : "light"}.png` });
        });
