import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
const revision = "a".repeat(64);
const installed = {
    declaration: {
        key: "github-panel",
        name: "GitHub",
        version: "0.1.1",
        description: "Checks and commits in your board",
        capabilities: ["panels.render", "signals.read"],
    },
    revision,
    is_enabled: true,
    installation: { kind: "official", sha256: "b".repeat(64), store: { icon: "panel/icon-light.svg", icon_dark: "panel/icon-dark.svg" } },
};
const official = [{ key: "github-panel", name: "GitHub", description: "Checks and commits in your board", repository: "publisher/github-app" }];
for (const width of [1440, 390]) {
    test(`official install receipt and mobile-safe detail at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = false;
        let writes = 0;
        await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official, installed: saved ? [installed] : [] } }));
        await page.route("**/settings/apps/packages/official/github-panel/install", async (route) => {
            writes++;
            expect(route.request().postDataJSON()).toEqual({ expected_revision: null });
            saved = true;
            await route.fulfill({ json: { app: installed } });
        });
        await page.route("**/apps/packages/**/panel/icon-*.svg", (route) =>
            route.fulfill({
                contentType: "image/svg+xml",
                body: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
                    <circle cx="10" cy="10" r="8" fill="${route.request().url().endsWith("icon-dark.svg") ? "white" : "black"}"/></svg>`,
            })
        );
        await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
        await page.getByRole("button", { name: "Download and install", exact: true }).click();
        await expect(page.getByRole("button", { name: "Update", exact: true })).toBeVisible();
        expect(writes).toBe(1);
        await page.getByRole("button", { name: "App details: GitHub" }).click();
        await expect(page.getByRole("heading", { name: "Permissions" })).toBeVisible();
        await expect(page.getByText("signals.read", { exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.evaluate(() => document.documentElement.classList.add("dark"));
        await expect(page.locator("img[src$='icon-dark.svg']")).toBeVisible();
        await expect(page.locator("img[src$='icon-light.svg']")).toBeHidden();
        await page.screenshot({ path: `.verification/app-store-detail-dark-${width}.png`, fullPage: true });
        await page.evaluate(() => document.documentElement.classList.remove("dark"));
        await expect(page.locator("img[src$='icon-light.svg']")).toBeVisible();
        await page.screenshot({ path: `.verification/app-store-detail-${width}.png`, fullPage: true });
    });
}
test("uncertain install locks writes until current readback", async ({ page }) => {
    let writes = 0;
    await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official, installed: [] } }));
    await page.route("**/settings/apps/packages/official/github-panel/install", async (route) => {
        writes++;
        await route.fulfill({ json: { app: { ...installed, revision: "invalid" } } });
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
    await page.getByRole("button", { name: "Download and install", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download and install", exact: true })).toBeDisabled();
    expect(writes).toBe(1);
});
test("ordinary board member cannot install centrally", async ({ page }) => {
    let requests = 0;
    await page.route("**/settings/apps/packages**", async (route) => {
        requests++;
        await route.fulfill({ json: {} });
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html?owner");
    await expect(page.getByText("An instance administrator must install apps.")).toBeVisible();
    expect(requests).toBe(0);
});

// Supply files extracted from SHA-256-verified official release ZIPs.
const officialPackageRoot = process.env.LANGBOARD_OFFICIAL_PACKAGE_ROOT;
for (const app of ["github", "glitchtip", "dokploy"]) {
    for (const width of [1440, 390]) {
        test(`published ${app} package icons load in both themes at ${width}`, async ({ page }) => {
            test.skip(!officialPackageRoot, "Requires verified official release package assets");
            const packageRoot = join(officialPackageRoot!, app);
            const manifest = JSON.parse(readFileSync(join(packageRoot, "manifest.json"), "utf8"));
            const receipt = { ...installed, declaration: manifest.declaration, installation: { ...installed.installation, store: manifest.store } };
            await page.setViewportSize({ width, height: 900 });
            await page.route("**/settings/apps/packages", (route) =>
                route.fulfill({
                    json: {
                        official: [
                            {
                                key: manifest.declaration.key,
                                name: manifest.declaration.name,
                                description: manifest.declaration.description,
                                repository: `yamonco/langboard-app-${app}`,
                            },
                        ],
                        installed: [receipt],
                    },
                })
            );
            await page.route("**/apps/packages/**/panel/icon-*.svg", (route) => {
                const asset = new URL(route.request().url()).pathname.split("/").pop()!;
                return route.fulfill({ contentType: "image/svg+xml", body: readFileSync(join(packageRoot, "panel", asset)) });
            });
            await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
            await page.getByRole("button", { name: `App details: ${manifest.declaration.name}` }).click();
            for (const dark of [false, true, false]) {
                await page.evaluate((enabled) => document.documentElement.classList.toggle("dark", enabled), dark);
                const active = page.locator(`img[src$="icon-${dark ? "dark" : "light"}.svg"]:visible`);
                await expect(active).toHaveCount(1);
                await expect
                    .poll(() => active.evaluate((image) => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))
                    .toBe(true);
                await expect(page.locator(`img[src$="icon-${dark ? "light" : "dark"}.svg"]`)).toBeHidden();
                expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
                await page.screenshot({ path: `.verification/published-${app}-${dark ? "dark" : "light"}-${width}.png`, fullPage: true });
            }
        });
    }
}
