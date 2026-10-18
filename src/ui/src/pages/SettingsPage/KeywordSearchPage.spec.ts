import { expect, test } from "@playwright/test";

for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
]) {
    test(`native keyword settings save and recover at ${viewport.width}`, async ({ page }, testInfo) => {
        await page.setViewportSize(viewport);
        const config = {
            revision: 0,
            mode: "native",
            reads_enabled: false,
            indexing_enabled: false,
            selected_connection_uid: null,
            connections: [],
            legacy_pending_cleanup: 1,
        };
        let saved: unknown;
        let reject = true;
        await page.route("**/settings/keyword-search", async (route) => {
            if (route.request().method() === "PUT") {
                saved = route.request().postDataJSON();
                if (reject) {
                    await route.fulfill({ status: 409, json: {} });
                    return;
                }
            }
            await route.fulfill({ json: config });
        });
        await page.route("**/secret-references/fixture", (route) => route.fulfill({ json: { reference: { scope: "instance", revision: 2 } } }));
        await page.goto("/src/pages/SettingsPage/KeywordSearchPage.fixture.html");
        await expect(page.getByRole("heading", { name: "Keyword search" })).toBeVisible();
        await page.getByLabel("HTTPS endpoint").fill("https://search.example.invalid");
        await page.getByLabel("Concrete index").fill("documents");
        await page.getByLabel("Instance SecretRef").fill("secret://ref/fixture");
        await page.getByLabel("Enable supplemental reads").check();
        await page.getByRole("button", { name: "Save settings", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        expect(saved).toMatchObject({
            expected_revision: 0,
            secret_ref: "secret://ref/fixture",
            secret_revision: 2,
            reads_enabled: true,
            indexing_enabled: false,
        });
        reject = false;
        await page.getByRole("button", { name: "Reload settings" }).click();
        await expect(page.getByRole("alert")).toHaveCount(0);
        await expect(page.getByLabel("Enable supplemental reads")).not.toBeChecked();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath(`keyword-settings-${viewport.width}.png`), fullPage: true });
    });
}

test("secure OOB reference completion and honest unavailable preflight", async ({ page }) => {
    const connection = {
        uid: "fixture",
        endpoint: "https://search.example.invalid",
        index: "documents",
        secret_ref: "secret://ref/fixture",
        secret_revision: 0,
        active: true,
        pending_cleanup: 2,
    };
    await page.route("**/settings/keyword-search", (route) =>
        route.fulfill({
            json: {
                revision: 3,
                mode: "native",
                reads_enabled: false,
                indexing_enabled: false,
                selected_connection_uid: "fixture",
                connections: [],
                selected_connection: connection,
                legacy_pending_cleanup: 0,
            },
        })
    );
    await page.route("**/settings/keyword-search/secret-input", (route) =>
        route.fulfill({ json: { input_uid: "input", input_url: "/secret-input/input" } })
    );
    await page.route("**/settings/keyword-search/secret-input/input", (route) =>
        route.fulfill({ json: { state: "completed", secret_ref: "secret://ref/newref" } })
    );
    await page.route("**/settings/keyword-search/connections/fixture/preflight", (route) =>
        route.fulfill({ json: { ok: false, code: "keyword_search_extra_required", data_mutated: false } })
    );
    await page.goto("/src/pages/SettingsPage/KeywordSearchPage.fixture.html");
    await expect(page.getByLabel("HTTPS endpoint")).toHaveValue(connection.endpoint);
    await page.getByRole("button", { name: "Create instance secret" }).click();
    await expect(page.getByRole("link", { name: "Open secure browser input" })).toHaveAttribute("href", "/secret-input/input");
    await page.getByRole("button", { name: "Use completed secret reference" }).click();
    await expect(page.getByLabel("Instance SecretRef")).toHaveValue("secret://ref/newref");
    await page.getByRole("button", { name: "Run preflight" }).click();
    await expect(page.getByRole("status")).toContainText("Connection checks did not pass");
    await page.getByText("Diagnostic result", { exact: true }).click();
    await expect(page.getByRole("status")).toContainText("keyword_search_extra_required");
});

test("pending save locks form controls and retains edits after conflict", async ({ page }) => {
    let release: () => void = () => {};
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/keyword-search", async (route) => {
        if (route.request().method() === "PUT") {
            await pending;
            await route.fulfill({ status: 409, json: {} });
            return;
        }
        await route.fulfill({
            json: {
                revision: 0,
                mode: "native",
                reads_enabled: false,
                indexing_enabled: false,
                selected_connection_uid: null,
                connections: [],
                legacy_pending_cleanup: 0,
            },
        });
    });
    await page.goto("/src/pages/SettingsPage/KeywordSearchPage.fixture.html");
    await page.getByLabel("HTTPS endpoint").fill("https://edited.example.invalid");
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.getByLabel("HTTPS endpoint")).toBeDisabled();
    release();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByLabel("HTTPS endpoint")).toBeEnabled();
    await expect(page.getByLabel("HTTPS endpoint")).toHaveValue("https://edited.example.invalid");
});
