import { expect, test } from "@playwright/test";

test("official SDK sends a scrubbed error envelope with deployment identity", async ({ page }) => {
    const envelopes: string[] = [];
    await page.route("https://errors.example.test/**", async (route) => {
        envelopes.push(route.request().postData() ?? "");
        await route.fulfill({ status: 200, json: {} });
    });
    await page.goto("/src/core/helpers/BrowserTelemetry.fixture.html?private-token=secret-query-value");
    await expect(page.locator("#state")).toHaveText("Ready");
    await page.getByRole("button").click();
    await expect.poll(() => envelopes.length).toBe(1);
    const envelope = envelopes[0];
    expect(envelope).toContain('"environment":"canary"');
    expect(envelope).toContain('"release":"test-exact-revision"');
    expect(envelope).toContain('"type":"Error"');
    expect(envelope).not.toContain("secret-test-value");
    expect(envelope).not.toContain("secret-query-value");
    expect(envelope).not.toContain("secret-extra-value");
    expect(envelope).not.toContain("secret-breadcrumb-value");
    expect(envelope).not.toContain("confidential@example.test");
    expect(envelope).not.toContain('"request":');
    expect(envelope).not.toContain('"user":');
    expect(envelope).not.toContain('"breadcrumbs":');
});

test("unconfigured telemetry does not load the SDK or contact an ingest server", async ({ page }) => {
    const telemetryRequests: string[] = [];
    page.on("request", (request) => {
        if (/sentry|errors\.example/.test(request.url())) telemetryRequests.push(request.url());
    });
    await page.goto("/src/core/helpers/BrowserTelemetry.fixture.html?disabled");
    await expect(page.locator("#state")).toHaveText("Ready");
    await page.getByRole("button").click();
    expect(telemetryRequests).toEqual([]);
});

test("a handled transport failure can be captured without application content", async ({ page }) => {
    const envelopes: string[] = [];
    await page.route("https://errors.example.test/**", async (route) => {
        envelopes.push(route.request().postData() ?? "");
        await route.fulfill({ status: 200, json: {} });
    });
    await page.goto("/src/core/helpers/BrowserTelemetry.fixture.html?handled");
    await expect(page.locator("#state")).toHaveText("Ready");
    await page.getByRole("button").click();
    await expect.poll(() => envelopes.length).toBe(1);
    expect(envelopes[0]).toContain("Application error (content omitted)");
    expect(envelopes[0]).not.toContain("secret-handled-transport-value");
});
