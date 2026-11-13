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

const transferRequest = {
    clientRequestID: "b5ca9084-f933-4d78-9576-df26aedb23c0",
    source: { cardUID: "fixture-card" },
    connectionUID: "fixture-connection",
    intent: { card_revision: 1, resource_uid: "fixture-resource", attachment_uid: null, attachment_revision: null, content_sha256: null },
    expectedAuthority: { revision: 1 },
};
const transferKey = "app-transfer:fixture-user:TestBoard01:tools";
const transferReceipt = {
    found: true,
    transfer_uid: "fixture-transfer",
    client_request_id: transferRequest.clientRequestID,
    card_uid: transferRequest.source.cardUID,
    connection_uid: transferRequest.connectionUID,
    intent: transferRequest.intent,
    receipt: { revision: 1, retry_revision: 1, acknowledgment: null, provider_outcome: null },
    delivery: { state: "failed", attempt_count: 1, last_error: null, history: [{ attempt: 1, outcome: "delivery_failed" }] },
};

for (const found of [false, true]) {
    test(`disabled receiving retains restored ${found ? "failed" : "unknown"} request and only reconciles reads`, async ({ page }) => {
        await page.addInitScript(({ key, request }) => sessionStorage.setItem(key, JSON.stringify(request)), { key: transferKey, request: transferRequest });
        const methods: string[] = [];
        await page.route("**/board/**", (route) => {
            methods.push(route.request().method());
            return route.fulfill({ headers, json: found ? transferReceipt : { found: false } });
        });
        await page.goto("/src/pages/AppsPage/AppStore.fixture.html?transfer=disabled&source=card");
        await expect(page.getByText("This app is not available to receive cards or attachments. You can still check the status of earlier transfers.")).toBeVisible();
        await expect(page.getByText(`Transfer request: ${transferRequest.clientRequestID}`)).toBeVisible();
        await expect(page.getByRole("button", { name: /Retry|Send transfer/ })).toHaveCount(0);
        await expect(page.getByRole("combobox")).toHaveCount(0);
        if (found) await expect(page.getByText("1: Delivery failed")).toBeVisible();
        else await expect(page.getByText("Result unknown. Refresh the same request before retrying.")).toBeVisible();
        await page.getByRole("button", { name: "Refresh transfer status" }).click();
        await expect.poll(() => methods.length).toBe(2);
        expect(methods).toEqual(["GET", "GET"]);
        expect(await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)!), transferKey)).toEqual(transferRequest);
    });
}

test("disabled receiving keeps historical outbox receipts accessible", async ({ page }) => {
    const requests: { method: string; path: string }[] = [];
    await page.route("**/board/**", (route) => {
        const path = new URL(route.request().url()).pathname.replace(/^\/api(?=\/)/, "");
        requests.push({ method: route.request().method(), path });
        return route.fulfill({ headers, json: path.includes("by-request") ? transferReceipt : { items: [transferReceipt], next_cursor: null } });
    });
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html?transfer=disabled");
    await expect(page.getByText("Drop a card or attachment on an app icon.")).toHaveCount(0);
    await expect(page.getByText("No transfers have been sent to this app.")).toHaveCount(0);
    await page.getByRole("button", { name: "fixture-card: Failed" }).click();
    await expect(page.getByText("1: Delivery failed")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry transfer delivery" })).toHaveCount(0);
    expect(requests).toEqual([
        { method: "GET", path: "/board/TestBoard01/apps/tools/transfers" },
        { method: "GET", path: `/board/TestBoard01/app-transfers/by-request/${transferRequest.clientRequestID}` },
    ]);
});

test("outbox empty text follows successful loads and disappears during refresh or errors", async ({ page }) => {
    let respond: (() => void) | undefined;
    let fail = false;
    await page.route("**/board/**/apps/tools/transfers**", async (route) => {
        await new Promise<void>((resolve) => { respond = resolve; });
        await route.fulfill({ headers, status: fail ? 503 : 200, json: fail ? {} : { items: [], next_cursor: null } });
    });
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html?transfer=disabled");
    const empty = page.getByText("No transfers have been sent to this app.");
    await expect(page.getByRole("status")).toHaveText("Loading...");
    await expect(empty).toHaveCount(0);
    await expect.poll(() => typeof respond).toBe("function");
    respond!();
    respond = undefined;
    await expect(empty).toBeVisible();
    await page.getByRole("button", { name: "Refresh transfer status" }).click();
    await expect(page.getByRole("status")).toHaveText("Loading...");
    await expect(empty).toHaveCount(0);
    fail = true;
    await expect.poll(() => typeof respond).toBe("function");
    respond!();
    respond = undefined;
    await expect(page.getByRole("alert")).toHaveText("Refresh this request to reconcile its result.");
    await expect(empty).toHaveCount(0);
    await page.getByRole("button", { name: "Refresh transfer status" }).click();
    await expect(page.getByRole("status")).toHaveText("Loading...");
    fail = false;
    await expect.poll(() => typeof respond).toBe("function");
    respond!();
    respond = undefined;
    await expect(empty).toBeVisible();
});

test("enabled receiving offers drop instructions", async ({ page }) => {
    await page.route("**/board/**/apps/tools/transfers**", (route) => route.fulfill({ headers, json: { items: [], next_cursor: null } }));
    await page.goto("/src/pages/AppsPage/AppStore.fixture.html?transfer=enabled");
    await expect(page.getByText("Drop a card or attachment on an app icon.")).toBeVisible();
    await expect(page.getByText("No transfers have been sent to this app.")).toBeVisible();
});

for (const availability of ["disabled", "unspecified"]) {
    test(`${availability} receiving does not review or create a new dropped transfer`, async ({ page }) => {
        const requests: string[] = [];
        await page.route("**/board/**", (route) => {
            requests.push(route.request().method());
            return route.fulfill({ headers, json: {} });
        });
        await page.goto(`/src/pages/AppsPage/AppStore.fixture.html?transfer=${availability}&source=card`);
        await expect(page.getByText("This app is not available to receive cards or attachments. You can still check the status of earlier transfers.")).toBeVisible();
        await expect(page.getByRole("button", { name: "Send transfer" })).toHaveCount(0);
        await expect(page.getByRole("combobox")).toHaveCount(0);
        expect(requests).toEqual([]);
        expect(await page.evaluate((key) => sessionStorage.getItem(key), transferKey)).toBeNull();
    });
}
