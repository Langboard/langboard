import { expect, test } from "@playwright/test";
const fixture = "/src/pages/SettingsPage/MailSettings.fixture.html";
const configuration = {
    server: "smtp.example.test",
    port: 587,
    starttls: true,
    ssl_tls: false,
    sender: "mail@example.test",
    username_configured: true,
    password_configured: true,
    authentication: "password",
    source: "environment",
};
const diagnostic = {
    checked_at: "2026-10-25T09:30:00+00:00",
    success: true,
    stages: {
        dns: { status: "passed", code: null },
        tcp: { status: "passed", code: null },
        tls: { status: "passed", code: null },
        auth: { status: "passed", code: null },
    },
    smtp_send_acceptance: "not_checked",
    inbox_delivery: "not_checked",
};
const headers = {
    "Access-Control-Allow-Origin": "http://127.0.0.1:4237",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "authorization,content-type",
};
for (const width of [1280, 390]) {
    test(`explicit safe diagnostics and keyboard retry at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        let calls = 0;
        await page.route("**/settings/mail/diagnostics", async (r) => {
            if (r.request().method() === "OPTIONS") return r.fulfill({ status: 204, headers });
            calls++;
            await r.fulfill({
                headers,
                json:
                    calls === 1
                        ? {
                              ...diagnostic,
                              success: false,
                              stages: { ...diagnostic.stages, auth: { status: "failed", code: "authentication_failed" } },
                          }
                        : diagnostic,
            });
        });
        await page.goto(fixture);
        await expect(page.getByTestId("native-mail-route")).toHaveText("/settings/mail");
        await expect(page.getByText(configuration.server, { exact: true })).toBeVisible();
        expect(calls).toBe(0);
        expect(await page.locator("input").count()).toBe(0);
        const button = page.getByRole("button", { name: "Test connection", exact: true });
        await button.focus();
        await page.keyboard.press("Enter");
        await expect(page.getByText(/Authentication failed\./)).toBeVisible();
        await expect(page.locator("time")).toHaveAttribute("datetime", diagnostic.checked_at);
        await expect(page.getByText(/SMTP message submission and inbox delivery have not been checked/)).toBeVisible();
        await button.click();
        await expect(page.getByText("Connection check passed", { exact: true })).toBeVisible();
        expect(calls).toBe(2);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        if (width === 390) await page.screenshot({ path: "../../.verification/card-work-wave-20261025/mail-ui/mail-mobile.png", fullPage: true });
    });
}
for (const transition of ["none", "Change actor", "Change session", "Revoke admin"]) {
    for (const status of [200, 401, 403]) {
        test(`duplicate prevention and late ${status} after ${transition}`, async ({ page }) => {
            await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
            let release!: () => void;
            const gate = new Promise<void>((r) => (release = r));
            let calls = 0;
            await page.route("**/settings/mail/diagnostics", async (r) => {
                if (r.request().method() === "OPTIONS") return r.fulfill({ status: 204, headers });
                calls++;
                await gate;
                await r.fulfill({ headers, status, json: diagnostic });
            });
            await page.goto(fixture);
            const button = page.getByRole("button", { name: "Test connection", exact: true });
            await button.evaluate((e) => {
                e.dispatchEvent(new MouseEvent("click", { bubbles: true }));
                e.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            });
            await expect(page.getByRole("button", { name: "Testing connection…" })).toBeDisabled();
            await expect.poll(() => calls).toBe(1);
            if (transition !== "none") await page.getByRole("button", { name: transition, exact: true }).click();
            release();
            if (transition === "none") {
                if (status === 200) await expect(page.locator("time")).toBeVisible();
                else {
                    await expect(page.getByRole("alert")).toContainText("Access expired or was denied");
                    await expect(page.getByText(configuration.server, { exact: true })).toHaveCount(0);
                }
            } else {
                if (transition === "Revoke admin") {
                    await expect(page.getByRole("alert")).toHaveText("Administrator access required.");
                    await expect(page.getByText(configuration.server, { exact: true })).toHaveCount(0);
                } else {
                    await expect(page.getByRole("button", { name: "Test connection", exact: true })).toBeEnabled();
                    await expect(page.getByRole("alert")).toHaveCount(0);
                }
                await expect(page.locator("time")).toHaveCount(0);
            }
            expect(calls).toBe(1);
        });
    }
}
test("configuration failure retries and unsafe errors are hidden", async ({ page }) => {
    let calls = 0;
    await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, status: ++calls === 1 ? 500 : 200, json: configuration }));
    await page.route("**/settings/mail/diagnostics", (r) =>
        r.fulfill({
            headers,
            json: { ...diagnostic, success: false, stages: { ...diagnostic.stages, dns: { status: "failed", code: "secret-token-response" } } },
        })
    );
    await page.goto(fixture);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.getByRole("button", { name: "Test connection", exact: true }).click();
    await expect(page.getByText(/Connection check failed\./)).toBeVisible();
    await expect(page.getByText("secret-token-response")).toHaveCount(0);
});

test("non-administrators cannot load configuration or diagnose", async ({ page }) => {
    const requests: string[] = [];
    await page.route("**/settings/mail/**", (route) => {
        requests.push(route.request().url());
        return route.fulfill({ headers, json: configuration });
    });
    await page.goto(`${fixture}?member`);
    await expect(page.getByRole("alert")).toHaveText("Administrator access required.");
    await expect(page.getByRole("button", { name: "Test connection", exact: true })).toHaveCount(0);
    expect(requests).toEqual([]);
});
