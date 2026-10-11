import { expect, test } from "@playwright/test";
for (const width of [1280, 390]) {
    test(`connection consent is explicit, fenced and recoverable at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        const writes: unknown[] = [];
        let reads = 0;
        let conflict = true;
        let scopes: string[] = [];
        let revision: number | null = null;
        const headers = {
            "Access-Control-Allow-Origin": "http://127.0.0.1:4216",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET,PUT,OPTIONS",
            "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
        };
        await page.route("**/settings/apps/connections/connection-one/mcp-consent", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            if (route.request().method() === "PUT") {
                writes.push(route.request().postDataJSON());
                if (conflict) return route.fulfill({ status: 409, headers, json: { detail: "conflict" } });
                scopes = route.request().postDataJSON().scopes;
                revision = (revision ?? 0) + 1;
            } else reads++;
            return route.fulfill({
                headers,
                json: {
                    connection_uid: "connection-one",
                    revision,
                    scopes,
                    available_scopes: ["read", "write", "automation"],
                    app_revision: "a".repeat(64),
                    identity_revision: "b".repeat(64),
                    execution_supported: false,
                },
            });
        });
        await page.goto("/src/pages/SettingsPage/AppMcpConsent.fixture.html");
        const read = page.getByRole("checkbox", { name: "Read external service", exact: true });
        await expect(read).not.toBeChecked();
        await expect(page.getByRole("status")).toContainText("not available");
        expect(writes).toHaveLength(0);
        await read.check();
        await page.getByRole("button", { name: "Save reviewed permissions" }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(read).toBeChecked();
        await expect(page.getByRole("button", { name: "Save reviewed permissions" })).toBeDisabled();
        expect(writes).toEqual([
            { scopes: ["read"], expected_revision: null, expected_app_revision: "a".repeat(64), expected_identity_revision: "b".repeat(64) },
        ]);
        conflict = false;
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(read).not.toBeChecked();
        await read.check();
        await page.getByRole("button", { name: "Save reviewed permissions" }).click();
        await expect(page.getByRole("button", { name: "Save reviewed permissions" })).toBeEnabled();
        expect(writes).toHaveLength(2);
        await page.getByRole("button", { name: "Clear selection" }).click();
        await page.getByRole("button", { name: "Save reviewed permissions" }).click();
        await expect(page.getByRole("button", { name: "Save reviewed permissions" })).toBeEnabled();
        expect(writes.at(-1)).toMatchObject({ scopes: [], expected_revision: 1 });
        expect(scopes).toEqual([]);
        expect(reads).toBe(4);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: `.verification/mcp-consent-${width}.png`, fullPage: true });
    });
}
