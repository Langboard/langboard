import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
    test(`explicit module group create, edit and ungroup at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        const writes: { path: string; body: Record<string, unknown> }[] = [];
        let groups: Record<string, unknown>[] = [];
        const revision = "a".repeat(64);
        await page.route("**/settings/apps/compositions**", async (route) => {
            const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:4218", "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "content-type,authorization,content-encoding" };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            if (route.request().method() === "GET") return route.fulfill({ headers, json: { compositions: groups, modules: [{ key: "github", name: "GitHub", version: "1", builtin: true, is_enabled: true }, { key: "independent-panel", name: "Independent Panel", version: "1.0.0", builtin: false, is_enabled: true }] } });
            const body = route.request().postDataJSON();
            const path = new URL(route.request().url()).pathname;
            writes.push({ path, body });
            groups = path.endsWith("ungroup") ? [] : [{ ...body, revision, generation: 1 }];
            await route.fulfill({ headers, json: {} });
        });
        await page.goto("/src/pages/SettingsPage/AppGroups.fixture.html");
        await page.getByRole("button", { name: "Create group", exact: true }).click();
        await page.getByLabel("Group key", { exact: true }).fill("review-suite");
        await page.getByLabel("Name", { exact: true }).fill("Review suite");
        await page.getByLabel("Description", { exact: true }).fill("Independent modules in one tile.");
        await page.getByRole("checkbox", { name: /GitHub/ }).check();
        await page.getByRole("checkbox", { name: /Independent Panel/ }).check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("status")).toHaveText("App groups saved.");
        expect(writes[0].body).toEqual({ key: "review-suite", name: "Review suite", description: "Independent modules in one tile.", module_keys: ["github", "independent-panel"], expected_revision: null });
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await expect(page.getByLabel("Group key", { exact: true })).toBeDisabled();
        await page.getByLabel("Name", { exact: true }).fill("Review tools");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Review tools", exact: true })).toBeVisible();
        expect(writes[1].body.expected_revision).toBe(revision);
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await page.getByRole("button", { name: "Ungroup", exact: true }).click();
        expect(writes).toHaveLength(2);
        await page.getByRole("button", { name: "Confirm", exact: true }).click();
        await expect(page.getByRole("article")).toHaveCount(0);
        expect(writes[2]).toEqual({ path: "/settings/apps/compositions/review-suite/ungroup", body: { expected_revision: revision } });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("failed mutation preserves draft and blocks repeat submission until reload", async ({ page }) => {
    let writes = 0;
    await page.route("**/settings/apps/compositions", async (route) => {
        const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:4218", "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "content-type,authorization,content-encoding" };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (route.request().method() === "GET") return route.fulfill({ headers, json: { compositions: [], modules: [{ key: "other", name: "Other", version: "1", builtin: false, is_enabled: true }] } });
        writes++;
        await route.fulfill({ headers, status: 409, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/AppGroups.fixture.html");
    await page.getByRole("button", { name: "Create group", exact: true }).click();
    await page.getByLabel("Group key", { exact: true }).fill("other-suite");
    await page.getByLabel("Name", { exact: true }).fill("Other suite");
    await page.getByLabel("Description", { exact: true }).fill("Description");
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Other suite");
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    expect(writes).toBe(1);
    await page.getByRole("button", { name: "Reload groups and discard draft", exact: true }).click();
    await expect(page.getByRole("button", { name: "Create group", exact: true })).toBeVisible();
    await expect(page.getByLabel("Name", { exact: true })).toHaveCount(0);
});

test("a non-admin makes no composition request and sees no admin editor", async ({ page }) => {
    let requests = 0;
    page.on("request", (request) => { if (request.url().includes("/settings/apps/compositions")) requests++; });
    await page.goto("/src/pages/SettingsPage/AppGroups.fixture.html?owner");
    await expect(page.getByRole("heading")).toHaveCount(0);
    expect(requests).toBe(0);
});

test("an older host without module discovery exposes recovery and no mutation editor", async ({ page }) => {
    await page.route("**/settings/apps/compositions", route => route.fulfill({ headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:4218", "Access-Control-Allow-Credentials": "true" }, json: { compositions: [] } }));
    await page.goto("/src/pages/SettingsPage/AppGroups.fixture.html");
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Create group", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
});
