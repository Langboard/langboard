import { expect, test } from "@playwright/test";
const module = {
    key: "tools",
    name: "Legacy tools module",
    description: "Independent authority",
    version: "2.0.0",
    capabilities: ["panels.render"],
    is_installed: true,
    source: "registered",
    is_enabled: true,
};
const group = {
    key: "tools",
    kind: "composition",
    composition_key: "tools",
    name: "Explicit tools suite",
    description: "Selected modules",
    is_installed: true,
    modules: [module, { ...module, key: "disabled", name: "Disabled module", is_enabled: false }],
};
const headers = {
    "Access-Control-Allow-Origin": "http://127.0.0.1:4293",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
};
for (const width of [1440, 390]) {
    test(`logical catalog and exact module shortcuts preserve identity at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const requests: URL[] = [];
        await page.route("**/account/app-preferences", (route) =>
            route.fulfill({ headers, json: { revision: 1, pinned_apps: [{ key: "tools", name: module.name }] } })
        );
        await page.route("**/apps/catalog**", (route) => {
            const url = new URL(route.request().url());
            requests.push(url);
            const logical = url.searchParams.get("include_compositions") === "true" || url.searchParams.has("composition_key");
            const selected = url.searchParams.has("composition_key")
                ? [group]
                : url.searchParams.has("key") || url.searchParams.has("after")
                  ? [module]
                  : logical
                    ? [group]
                    : [module];
            return route.fulfill({
                headers,
                json: {
                    items: selected,
                    official: logical ? [group] : [module],
                    next_cursor:
                        !url.searchParams.has("key") && !url.searchParams.has("composition_key") && !url.searchParams.has("after")
                            ? "tools~composition"
                            : null,
                    effective_mode: "approved_only",
                    can_manage_installation: false,
                },
            });
        });
        await page.goto("/src/pages/AppsPage/AppStore.fixture.html");
        await expect(page.getByRole("heading", { name: group.name, exact: true })).toHaveCount(1);
        expect(requests[0].searchParams.get("include_compositions")).toBe("true");
        await page
            .getByRole("link")
            .filter({ has: page.getByRole("heading", { name: group.name, exact: true }) })
            .click();
        await expect(page.getByRole("region", { name: "App components" })).toBeVisible();
        expect(requests.at(-1)?.searchParams.get("composition_key")).toBe("tools");
        expect(requests.at(-1)?.searchParams.has("key")).toBe(false);
        await expect(page.getByText("Disabled by administrator", { exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Pin to rail", exact: true })).toHaveCount(0);
        await expect(page.getByRole("link", { name: "View Disabled module" })).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await Promise.all([
            page.waitForRequest((request) => new URL(request.url()).searchParams.get("key") === "tools"),
            page.getByRole("link", { name: "View Legacy tools module", exact: true }).click(),
        ]);
        await expect(page.getByRole("heading", { name: module.name, exact: true })).toBeVisible();
        await expect.poll(() => requests.at(-1)?.searchParams.get("key")).toBe("tools");
        expect(requests.at(-1)?.searchParams.has("composition_key")).toBe(false);
        await expect(page.getByRole("button", { name: "Unpin from rail", exact: true })).toBeVisible();
        await page.getByRole("link", { name: "All apps", exact: true }).click();
        await Promise.all([
            page.waitForRequest((request) => new URL(request.url()).searchParams.get("after") === "tools~composition"),
            page.getByRole("link", { name: "Next page", exact: true }).click(),
        ]);
        await expect.poll(() => requests.at(-1)?.searchParams.get("after")).toBe("tools~composition");
        await expect(page.getByRole("heading", { name: module.name, exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: group.name, exact: true })).toBeVisible();
        expect(requests.every((request) => request.searchParams.get("include_compositions") === "true")).toBe(true);
    });
}
test("policy-disabled logical detail exposes no configuration or module actions", async ({ page }) => {
    await page.route("**/account/app-preferences", (route) => route.fulfill({ headers, json: { revision: 1, pinned_apps: [] } }));
    await page.route("**/apps/catalog**", (route) =>
        route.fulfill({ headers, json: { items: [], official: [], next_cursor: null, effective_mode: "disabled", can_manage_installation: false } })
    );
    await page.goto(
        "/src/pages/AppsPage/AppStore.fixture.html?initial=" + encodeURIComponent("/apps/tools?composition_key=tools&project_uid=TestBoard01")
    );
    await expect(page.getByRole("status")).toHaveText("Apps are disabled by the current policy.");
    await expect(page.getByRole("link", { name: "Configure on this board" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "App components" })).toHaveCount(0);
});

test("modern AppStore remains compatible with a host returning the legacy flat contract", async ({ page }) => {
    const requests: URL[] = [];
    await page.route("**/account/app-preferences", (route) => route.fulfill({ headers, json: { revision: 1, pinned_apps: [] } }));
    await page.route("**/apps/catalog**", (route) => {
        const url = new URL(route.request().url());
        requests.push(url);
        const next = url.searchParams.has("after");
        return route.fulfill({
            headers,
            json: {
                items: next ? [{ ...module, key: "zeta", name: "Zeta standalone" }] : [module],
                official: [],
                next_cursor: next || url.searchParams.has("key") ? null : "tools",
                effective_mode: "approved_only",
                can_manage_installation: false,
            },
        });
    });
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html");
    await expect(page.getByRole("heading", { name: module.name, exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: group.name, exact: true })).toHaveCount(0);
    await page.getByRole("link", { name: "Next page", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Zeta standalone", exact: true })).toBeVisible();
    expect(requests.at(-1)?.searchParams.get("after")).toBe("tools");
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html?initial=" + encodeURIComponent("/apps/tools?project_uid=TestBoard01"));
    await expect(page.getByRole("heading", { name: module.name, exact: true })).toBeVisible();
    expect(requests.at(-1)?.searchParams.get("key")).toBe("tools");
    expect(requests.at(-1)?.searchParams.has("composition_key")).toBe(false);
    await expect(page.getByRole("button", { name: "Pin to rail", exact: true })).toBeVisible();
});
