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
        expect(await page.locator("input[type=password]").count()).toBe(0);
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
        if (width === 390)
            await page.screenshot({ path: "../../.verification/card-work-wave-20261025/mail-operations-ui/mail-mobile.png", fullPage: true });
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

const delivery = {
    key: "notification:01234567890",
    source: "notification",
    uid: "01234567890",
    created_at: "2026-10-25T09:30:00Z",
    claimed_at: "2026-10-25T09:31:00Z",
    sent_at: null,
    status: "uncertain",
    recipient_masked: "***@***",
    trigger: "card_assigned",
    template: "card_assigned",
    subject: null,
    safe_error_code: "worker_outcome_unknown",
    attempt_evidence: "claimed",
    inbox_delivery: "unverified",
    retry_history: null,
    linked_ids: null,
};
const coverage = {
    sources: ["notification", "project_activity"],
    subject: "not_persisted",
    retry_history: "not_persisted",
    inbox_delivery: "unavailable",
    direct_email_paths: "not_covered",
};

test("single recipient, explicit send, ambiguous network outcome and no automatic retry", async ({ page }) => {
    await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
    let calls = 0;
    await page.route("**/settings/mail/test-message", async (r) => {
        if (r.request().method() === "OPTIONS") return r.fulfill({ headers, status: 204 });
        calls++;
        expect(r.request().postDataJSON()).toEqual({ recipient: "operator@example.test" });
        if (calls === 1) return r.abort("failed");
        return r.fulfill({
            headers,
            json: { checked_at: diagnostic.checked_at, smtp_send_acceptance: "accepted", inbox_delivery: "not_checked", code: null },
        });
    });
    await page.goto(fixture);
    const recipient = page.getByRole("textbox", { name: "Recipient", exact: true });
    const send = page.getByRole("button", { name: "Send test message", exact: true });
    await expect(send).toBeEnabled();
    expect(calls).toBe(0);
    for (const invalid of ["a@example.test,b@example.test", "Person <a@example.test>", "a@example.test;b@example.test"]) {
        await recipient.fill(invalid);
        await send.click();
        expect(calls).toBe(0);
    }
    await recipient.fill("operator@example.test");
    await send.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText(/Outcome unknown. The message may have been sent/)).toBeVisible();
    await expect(send).toBeEnabled();
    expect(calls).toBe(1);
    await send.click();
    await expect(page.getByText(/SMTP server accepted the message/)).toBeVisible();
    await expect(page.getByText(/Inbox delivery is unverified/)).toBeVisible();
    expect(calls).toBe(2);
});

for (const status of [400, 401, 403, 422, 500]) {
    test(`test send terminal ${status} never replays or exposes raw errors`, async ({ page }) => {
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        const requests: string[] = [];
        await page.route("**/settings/mail/test-message", async (r) => {
            if (r.request().method() === "OPTIONS") return r.fulfill({ headers, status: 204 });
            requests.push("send");
            await r.fulfill({ headers, status, json: { detail: "private-smtp-token" } });
        });
        await page.route("**/auth/refresh", (r) => {
            requests.push("refresh");
            return r.fulfill({ headers, status: 500 });
        });
        await page.goto(fixture);
        await page.getByRole("textbox", { name: "Recipient", exact: true }).fill("operator@example.test");
        await page.getByRole("button", { name: "Send test message", exact: true }).click();
        if (status === 401 || status === 403) {
            await expect(page.getByRole("alert")).toContainText("Access expired or was denied");
            await expect(page.getByRole("textbox", { name: "Recipient", exact: true })).toHaveCount(0);
            await expect(page.getByRole("button", { name: "Load history", exact: true })).toHaveCount(0);
        } else if (status === 422 || status === 400) await expect(page.getByRole("alert")).toContainText("One valid email address");
        else await expect(page.getByText(/Outcome unknown. The message may have been sent/)).toBeVisible();
        await expect(page.getByText("private-smtp-token")).toHaveCount(0);
        expect(requests).toEqual(["send"]);
    });
}
for (const transition of ["none", "Change actor", "Change session", "Revoke admin"]) {
    test(`test send duplicate guard and delayed response after ${transition}`, async ({ page }) => {
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        let release!: () => void;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        let calls = 0;
        await page.route("**/settings/mail/test-message", async (r) => {
            if (r.request().method() === "OPTIONS") return r.fulfill({ headers, status: 204 });
            calls++;
            await gate;
            await r.fulfill({ headers, json: { smtp_send_acceptance: "accepted", inbox_delivery: "not_checked", code: null } });
        });
        await page.goto(fixture);
        await page.getByRole("textbox", { name: "Recipient", exact: true }).fill("operator@example.test");
        await page.getByRole("button", { name: "Send test message", exact: true }).evaluate((button) => {
            button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        await expect.poll(() => calls).toBe(1);
        await expect(page.getByRole("button", { name: "Sending test message…", exact: true })).toBeDisabled();
        if (transition !== "none") await page.getByRole("button", { name: transition, exact: true }).click();
        release();
        if (transition === "none") await expect(page.getByText(/SMTP server accepted the message/)).toBeVisible();
        else {
            if (transition === "Revoke admin") await expect(page.getByRole("alert")).toHaveText("Administrator access required.");
            else await expect(page.getByRole("button", { name: "Send test message", exact: true })).toBeEnabled();
            await expect(page.getByText(/SMTP server accepted the message/)).toHaveCount(0);
        }
        expect(calls).toBe(1);
    });
}
for (const width of [1280, 390]) {
    test(`bounded history, applied filters, safe detail and cursor reset at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        const queries: URLSearchParams[] = [];
        await page.route("**/settings/mail/deliveries?*", async (r) => {
            queries.push(new URL(r.request().url()).searchParams);
            const second = queries.length === 2;
            const items = second
                ? [{ ...delivery, key: "project_activity:11234567890", source: "project_activity", uid: "11234567890" }]
                : [delivery];
            await r.fulfill({ headers, json: { coverage, items, next_cursor: second ? null : "opaque+cursor/= value" } });
        });
        await page.route("**/settings/mail/deliveries/notification/*", (r) =>
            r.fulfill({
                headers,
                json: {
                    ...delivery,
                    safe_error_code: "private-token",
                    subject: "private-subject",
                    retry_history: ["private-retry"],
                    linked_ids: { card: "private-card" },
                },
            })
        );
        await page.goto(fixture);
        const load = page.getByRole("button", { name: "Load history", exact: true });
        await expect(load).toBeEnabled();
        expect(queries).toHaveLength(0);
        await page.getByRole("combobox", { name: "Status", exact: true }).selectOption("uncertain");
        await page.getByLabel("Created from (UTC)", { exact: true }).fill("2026-10-24");
        await page.getByLabel("Created before (UTC)", { exact: true }).fill("2026-10-26");
        await load.focus();
        await page.keyboard.press("Enter");
        await page.getByRole("button", { name: "View delivery 01234567890", exact: true }).click();
        const detail = page.getByRole("region", { name: "Delivery detail", exact: true });
        await expect(detail).toContainText("Worker claimed the record; SMTP outcome unverified");
        await expect(detail).toContainText("Historical subjects");
        await expect(detail).toContainText("Inbox delivery is unverified");
        for (const secret of ["private-token", "private-subject", "private-retry", "private-card"])
            await expect(page.getByText(secret)).toHaveCount(0);
        await page.getByRole("combobox", { name: "Status", exact: true }).selectOption("failed");
        await page.getByRole("button", { name: "Load 25 more", exact: true }).click();
        await expect(page.getByRole("button", { name: /View delivery/ })).toHaveCount(2);
        expect(Object.fromEntries(queries[0])).toEqual({
            limit: "25",
            view: "attempts",
            status: "uncertain",
            created_from: "2026-10-24T00:00:00.000Z",
            created_to: "2026-10-26T00:00:00.000Z",
        });
        expect(queries[1].get("cursor")).toBe("opaque+cursor/= value");
        expect(queries[1].get("status")).toBe("uncertain");
        await load.click();
        await expect(page.getByRole("button", { name: /View delivery/ })).toHaveCount(1);
        expect(queries[2].get("cursor")).toBeNull();
        expect(queries[2].get("status")).toBe("failed");
        await expect(detail).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        if (width === 390)
            await page.screenshot({ path: "../../.verification/card-work-wave-20261025/mail-operations-ui/history-mobile.png", fullPage: true });
    });
}

test("history date validation, empty state, missing detail and safe failure", async ({ page }) => {
    await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
    let calls = 0;
    await page.route("**/settings/mail/deliveries?*", (r) =>
        r.fulfill({
            headers,
            status: ++calls === 3 ? 400 : 200,
            json: { items: calls === 1 ? [] : [delivery], next_cursor: null, coverage, detail: "private-filter-error" },
        })
    );
    await page.route("**/settings/mail/deliveries/notification/*", (r) =>
        r.fulfill({ headers, status: 404, json: { detail: "private-history-error" } })
    );
    await page.goto(fixture);
    const load = page.getByRole("button", { name: "Load history", exact: true });
    await page.getByLabel("Created from (UTC)", { exact: true }).fill("2026-10-25");
    await page.getByLabel("Created before (UTC)", { exact: true }).fill("2026-10-25");
    await load.click();
    await expect(page.getByRole("alert")).toHaveText("End date must be later than start date.");
    expect(calls).toBe(0);
    await page.getByLabel("Created before (UTC)", { exact: true }).fill("2026-10-26");
    await load.click();
    await expect(page.getByText(/No records match/)).toBeVisible();
    await load.click();
    await page.getByRole("button", { name: "View delivery 01234567890", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText("This delivery record is no longer available.");
    await load.click();
    await expect(page.getByRole("alert")).toContainText("History could not be loaded");
    await expect(page.getByText(/private-(filter|history)-error/)).toHaveCount(0);
});
for (const transition of ["Change actor", "Change session", "Revoke admin"]) {
    test(`history late response fenced after ${transition}`, async ({ page }) => {
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        let release!: () => void;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        let calls = 0;
        await page.route("**/settings/mail/deliveries?*", async (r) => {
            calls++;
            await gate;
            await r.fulfill({ headers, json: { items: [delivery], next_cursor: "old-cursor", coverage } });
        });
        await page.goto(fixture);
        await page.getByRole("button", { name: "Load history", exact: true }).evaluate((button) => {
            button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        await expect.poll(() => calls).toBe(1);
        await expect(page.getByRole("combobox", { name: "Status", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: transition, exact: true }).click();
        release();
        if (transition !== "Revoke admin") await expect(page.getByRole("button", { name: "Load history", exact: true })).toBeEnabled();
        else await expect(page.getByRole("alert")).toHaveText("Administrator access required.");
        await expect(page.getByRole("button", { name: /View delivery/ })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Load 25 more", exact: true })).toHaveCount(0);
        expect(calls).toBe(1);
    });
}
test("history permission denial clears pending diagnostic late results", async ({ page }) => {
    await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/mail/diagnostics", async (r) => {
        if (r.request().method() === "OPTIONS") return r.fulfill({ headers, status: 204 });
        await gate;
        await r.fulfill({ headers, json: diagnostic });
    });
    await page.route("**/settings/mail/deliveries?*", (r) => r.fulfill({ headers, status: 403, json: { detail: "private-denial" } }));
    await page.goto(fixture);
    await page.getByRole("button", { name: "Test connection", exact: true }).click();
    await page.getByRole("button", { name: "Load history", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Access expired or was denied");
    release();
    await expect(page.getByText(configuration.server, { exact: true })).toHaveCount(0);
    await expect(page.getByText("Connection check passed", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Recipient", exact: true })).toHaveCount(0);
});

for (const status of [401, 403]) {
    test(`detail ${status} clears loaded history and configuration`, async ({ page }) => {
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        await page.route("**/settings/mail/deliveries?*", (r) => r.fulfill({ headers, json: { items: [delivery], next_cursor: null, coverage } }));
        await page.route("**/settings/mail/deliveries/notification/*", (r) => r.fulfill({ headers, status, json: { detail: "private-error" } }));
        await page.goto(fixture);
        await page.getByRole("button", { name: "Load history", exact: true }).click();
        await page.getByRole("button", { name: "View delivery 01234567890", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Access expired or was denied");
        await expect(page.getByText(configuration.server, { exact: true })).toHaveCount(0);
        await expect(page.getByText("***@***", { exact: false })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Send test message", exact: true })).toHaveCount(0);
    });
}
for (const transition of ["Change actor", "Change session", "Revoke admin"]) {
    test(`detail late response fenced after ${transition}`, async ({ page }) => {
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        await page.route("**/settings/mail/deliveries?*", (r) => r.fulfill({ headers, json: { items: [delivery], next_cursor: null, coverage } }));
        let release!: () => void;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        let calls = 0;
        await page.route("**/settings/mail/deliveries/notification/*", async (r) => {
            calls++;
            await gate;
            await r.fulfill({ headers, json: delivery });
        });
        await page.goto(fixture);
        await page.getByRole("button", { name: "Load history", exact: true }).click();
        await page.getByRole("button", { name: "View delivery 01234567890", exact: true }).click();
        await expect.poll(() => calls).toBe(1);
        await expect(page.getByRole("button", { name: "Load history", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: transition, exact: true }).click();
        const response = page.waitForResponse("**/settings/mail/deliveries/notification/*");
        release();
        await response;
        if (transition === "Revoke admin") await expect(page.getByRole("alert")).toHaveText("Administrator access required.");
        else await expect(page.getByRole("button", { name: "Load history", exact: true })).toBeEnabled();
        await expect(page.getByRole("region", { name: "Delivery detail", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: /View delivery/ })).toHaveCount(0);
    });
}
for (const [locale, sendLabel, historyLabel] of [
    ["en-US", "Send test message", "Load history"],
    ["ko-KR", "테스트 메일 보내기", "이력 조회"],
    ["ja-JP", "テストメールを送信", "履歴を読み込む"],
    ["zh-CN", "发送测试邮件", "加载历史"],
]) {
    test(`localized mail operations fit 390px in ${locale}`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.addInitScript((language) => localStorage.setItem("lang", language), locale);
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        await page.route("**/settings/mail/deliveries?*", (r) => r.fulfill({ headers, json: { items: [], next_cursor: null, coverage } }));
        await page.goto(fixture);
        await expect(page.getByRole("button", { name: sendLabel, exact: true })).toBeVisible();
        await page.getByRole("button", { name: historyLabel, exact: true }).click();
        await expect(page.getByRole("status")).toBeVisible();
        await expect(page.getByText(/settings\.mail\./)).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("failed and unknown SMTP responses expose only safe error categories", async ({ page }) => {
    await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
    let calls = 0;
    await page.route("**/settings/mail/test-message", (r) => {
        if (r.request().method() === "OPTIONS") return r.fulfill({ headers, status: 204 });
        return r.fulfill({
            headers,
            json: {
                checked_at: diagnostic.checked_at,
                smtp_send_acceptance: ++calls === 1 ? "failed" : "unknown",
                inbox_delivery: "not_checked",
                code: calls === 1 ? "smtp_rejected" : "private-token-error",
            },
        });
    });
    await page.goto(fixture);
    await page.getByRole("textbox", { name: "Recipient", exact: true }).fill("operator@example.test");
    const send = page.getByRole("button", { name: "Send test message", exact: true });
    await send.click();
    await expect(page.getByText("SMTP server rejected the message.", { exact: true })).toBeVisible();
    await expect(page.getByText(/SMTP submission failed/)).toBeVisible();
    await send.click();
    await expect(page.getByText(/Outcome unknown. The message may have been sent/)).toBeVisible();
    await expect(page.getByText("private-token-error")).toHaveCount(0);
    expect(calls).toBe(2);
});

for (const [locale, historyLabel, viewLabel, legacyLabel] of [
    ["en-US", "Load history", "History view", "Legacy outbox latest state"],
    ["ko-KR", "이력 조회", "이력 보기", "기존 아웃박스 최신 상태"],
    ["ja-JP", "履歴を読み込む", "履歴表示", "既存アウトボックスの最新状態"],
    ["zh-CN", "加载历史", "历史视图", "旧发件箱最新状态"],
]) {
    test(`actual attempt detail and distinct legacy view at 390px in ${locale}`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.addInitScript((language) => localStorage.setItem("lang", language), locale);
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        const attempt = {
            ...delivery,
            key: "smtp_attempt:01234567890",
            source: "smtp_attempt",
            record_kind: "smtp_attempt",
            started_at: "2026-10-25T10:00:00+00:00",
            completed_at: "2026-10-25T10:00:01+00:00",
            status: "failed",
            safe_error_code: "smtp_rejected",
            attempt_evidence: "smtp_rejected",
            subject: "Langboard SMTP test message",
            trigger: "smtp_test",
            outbox_key: "notification:01234567890",
            recorded_attempt_count: 3,
        };
        const queries: URLSearchParams[] = [];
        await page.route("**/settings/mail/deliveries?*", (r) => {
            const query = new URL(r.request().url()).searchParams;
            queries.push(query);
            return r.fulfill({ headers, json: { coverage, items: [query.get("view") === "legacy" ? delivery : attempt], next_cursor: null } });
        });
        await page.route("**/settings/mail/deliveries/smtp_attempt/*", (r) => r.fulfill({ headers, json: attempt }));
        await page.goto(fixture);
        await page.getByRole("button", { name: historyLabel, exact: true }).click();
        await page.getByRole("button", { name: /01234567890/ }).click();
        await expect(page.getByText("Langboard SMTP test message", { exact: true })).toBeVisible();
        await expect(page.getByText("smtp_test", { exact: true })).toBeVisible();
        await expect(page.getByText(attempt.completed_at, { exact: true })).toBeVisible();
        await expect(page.getByText(attempt.outbox_key, { exact: true })).toBeVisible();
        const provenance = {
            "en-US": "Multiple associated SMTP attempts recorded.",
            "ko-KR": "연결된 SMTP 시도가 여러 건 기록되었습니다.",
            "ja-JP": "関連するSMTP試行が複数記録されています。",
            "zh-CN": "已记录多次关联的SMTP尝试。",
        };
        await expect(page.getByText(provenance[locale as keyof typeof provenance], { exact: false })).toBeVisible();
        await page.getByRole("combobox", { name: viewLabel, exact: true }).selectOption({ label: legacyLabel });
        await page.getByRole("button", { name: historyLabel, exact: true }).click();
        expect(queries.map((query) => query.get("view"))).toEqual(["attempts", "legacy"]);
        expect(queries[1].get("cursor")).toBeNull();
        await expect(page.getByText("Langboard SMTP test message", { exact: true })).toHaveCount(0);
        await expect(page.getByText(/settings\.mail\./)).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

for (const [count, message] of [
    [null, "No shared outbox identity."],
    [0, "No associated SMTP attempts recorded."],
    [1, "One associated SMTP attempt recorded."],
    [3, "Multiple associated SMTP attempts recorded."],
] as const) {
    test(`recorded attempt count ${count} preserves unknown retry intent`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
        const item = { ...delivery, recorded_attempt_count: count, trigger: "signup", retry_history: null };
        await page.route("**/settings/mail/deliveries?*", (r) => r.fulfill({ headers, json: { items: [item], next_cursor: null } }));
        await page.route("**/settings/mail/deliveries/notification/*", (r) => r.fulfill({ headers, json: item }));
        await page.goto(fixture);
        await page.getByRole("button", { name: "Load history", exact: true }).click();
        await page.getByRole("button", { name: /01234567890/ }).click();
        await expect(page.getByText(message, { exact: false })).toBeVisible();
        const countRow = page.locator("dl > div").filter({ has: page.getByText("Recorded SMTP attempt count", { exact: true }) });
        await expect(countRow.locator("dd")).toHaveText(count === null ? "Not recorded / unavailable" : String(count));
        await expect(page.getByText("signup", { exact: true })).toBeVisible();
        await expect(page.getByText(/settings\.mail\./)).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        if (count === 3)
            await page.screenshot({ path: "../../.verification/mail-attempt-provenance-20261025/recorded-count-mobile.png", fullPage: true });
    });
}

for (const width of [1280, 390]) {
    for (const kind of ["board", "card", "denied"] as const) {
        test(`native related ${kind} detail reference at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 844 });
            await page.route("**/settings/mail/configuration", (r) => r.fulfill({ headers, json: configuration }));
            await page.route("**/settings/mail/deliveries?*", (r) => r.fulfill({ headers, json: { items: [delivery], next_cursor: null } }));
            await page.route("**/settings/mail/deliveries/notification/*", (r) =>
                r.fulfill({
                    headers,
                    json: {
                        ...delivery,
                        linked_ids: { card_id: "untrusted-id" },
                        resources:
                            kind === "denied"
                                ? []
                                : [{ kind, project_uid: "board/encoded", ...(kind === "card" ? { card_uid: "card?encoded" } : {}) }],
                    },
                })
            );
            await page.goto(fixture);
            await page.getByRole("button", { name: "Load history", exact: true }).click();
            await expect(page.getByRole("navigation", { name: "Related resources" })).toHaveCount(0);
            await page.getByRole("button", { name: "View delivery 01234567890", exact: true }).click();
            if (kind === "denied") {
                await expect(page.getByRole("region", { name: "Delivery detail" })).toBeVisible();
                await expect(page.getByRole("navigation", { name: "Related resources" })).toHaveCount(0);
            } else {
                const link = page.getByRole("link", { name: kind === "card" ? "Open card" : "Open board", exact: true });
                await expect(link).toHaveAttribute("href", kind === "card" ? "/board/board%2Fencoded/card%3Fencoded" : "/board/board%2Fencoded");
                await link.focus();
                await expect(link).toBeFocused();
            }
            await expect(page.getByText("untrusted-id")).toHaveCount(0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            if (kind === "card" && width === 390)
                await page.screenshot({ path: "../../.verification/mail-resource-authority-20261025/resources-mobile.png", fullPage: true });
        });
    }
}
