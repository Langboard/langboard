import { expect, test } from "@playwright/test";

for (const width of [390, 1440]) {
    test(`explicit grants save, reload and revoke at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 950 });
        const grants: Record<string, unknown> = {
            project_uid: "board-one",
            profile_uid: "compactor-one",
            can_read: false,
            can_compact: false,
            can_archive: false,
        };
        let puts = 0;
        let denied = false;
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.route("**/board/board-one/compactor/*/grant", async (route) => {
            if (denied) return route.fulfill({ status: 403, json: { code: "forbidden" } });
            if (route.request().method() === "PUT") {
                puts++;
                Object.assign(grants, route.request().postDataJSON());
            }
            return route.fulfill({ json: grants });
        });
        await page.goto("/compactor-grant-fixture.html");
        await page.evaluate(async () => {
            const path = "/src/i18n.ts";
            await (await import(path)).default.changeLanguage("en-US");
        });
        const read = page.getByRole("checkbox", { name: "Read board cards" });
        const compact = page.getByRole("checkbox", { name: "Compact comments" });
        const archive = page.getByRole("checkbox", { name: "Archive cards" });
        await expect(read).not.toBeChecked();
        await expect(compact).toBeDisabled();
        await expect(archive).toBeDisabled();
        expect(puts).toBe(0);
        await read.check();
        await compact.check();
        await archive.check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("status")).toHaveText("Authority saved and current permissions reloaded.");
        expect(puts).toBe(1);
        expect(grants).toMatchObject({ can_read: true, can_compact: true, can_archive: true });
        await read.uncheck();
        await expect(compact).not.toBeChecked();
        await expect(archive).not.toBeChecked();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("status")).toContainText("Authority saved");
        expect(grants).toMatchObject({ can_read: false, can_compact: false, can_archive: false });
        await read.check();
        denied = true;
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("checkbox")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
        denied = false;
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(read).not.toBeChecked();
        await expect(page.getByText("Automatic compaction requires read and compact authority", { exact: false })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        expect(errors).toEqual([]);
        await page.screenshot({ path: `.verification/compactor-board-grant-${width}.png`, fullPage: true });
    });

    test(`late responses cannot populate another profile at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 950 });
        let release: () => void = () => {};
        const pending = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route("**/board/board-one/compactor/*/grant", async (route) => {
            const first = route.request().url().includes("compactor-one");
            if (first) await pending;
            await route
                .fulfill({
                    json: {
                        project_uid: "board-one",
                        profile_uid: first ? "compactor-one" : "compactor-two",
                        can_read: first,
                        can_compact: first,
                        can_archive: first,
                    },
                })
                .catch(() => {});
        });
        await page.goto("/compactor-grant-fixture.html");
        await page.evaluate(async () => {
            const path = "/src/i18n.ts";
            await (await import(path)).default.changeLanguage("en-US");
        });
        await page.getByRole("button", { name: "Switch profile" }).click();
        await expect(page.getByRole("checkbox", { name: "Read board cards" })).not.toBeChecked();
        release();
        await expect(page.getByRole("group", { name: "Compactor two" })).toBeVisible();
        await expect(page.getByRole("checkbox", { name: "Read board cards" })).not.toBeChecked();
        await page.getByRole("button", { name: "Leave settings" }).click();
        await expect(page.getByRole("checkbox")).toHaveCount(0);
    });
}

test.beforeEach(async ({ page }) => {
    await page.route("**/compactor-grant-fixture.html", (route) =>
        route.fulfill({
            contentType: "text/html",
            body: `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div>
        <script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
        <script type="module" src="/src/pages/BoardPage/components/settings/internalBots/compactor-grant.fixture.tsx"></script></body></html>`,
        })
    );
});
