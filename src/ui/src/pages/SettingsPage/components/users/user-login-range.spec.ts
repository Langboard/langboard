import { test, expect, type Page } from "@playwright/test";
const fixture = "/src/pages/SettingsPage/components/users/group-directory.fixture.html?mode=user";
async function setup(page: Page, activityAt: string | null = "2026-10-23T02:30:00Z", identitySources?: ("oidc" | "scim")[]) {
    const reads: URL[] = [];
    let fail = false;
    await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.port === "4199") return route.continue();
        if (url.port !== "5381") return route.abort();
        const headers = {
            "access-control-allow-origin": "http://127.0.0.1:4199",
            "access-control-allow-credentials": "true",
            "access-control-allow-headers": "*",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (url.pathname.endsWith("/dashboard/projects")) return route.fulfill({ json: { projects: [], columns: [] }, headers });
        if (url.pathname.endsWith("/settings/roles"))
            return route.fulfill({ json: { setting_role_actions: ["user_read"], api_key_role_actions: [], mcp_role_actions: [] }, headers });
        if (url.pathname.endsWith("/settings/users/page")) {
            reads.push(url);
            if (fail) return route.fulfill({ status: 500, json: {}, headers });
            const filtered = url.searchParams.has("last_signed_in_from") || url.searchParams.has("last_signed_in_before");
            return route.fulfill({
                json: {
                    records: [
                        {
                            uid: "00000000001",
                            type: "user",
                            firstname: "Range",
                            lastname: "Person",
                            username: "range-person",
                            email: "range@example.invalid",
                            is_admin: false,
                            activated_at: "2026-01-01T00:00:00Z",
                            created_at: "2026-01-01T00:00:00Z",
                            updated_at: "2026-01-01T00:00:00Z",
                            last_signed_in_at: "2026-10-24T01:00:00Z",
                            latest_business_activity_at: activityAt,
                            ...(identitySources === undefined ? {} : { identity_sources: identitySources }),
                            account_status: "active",
                            setting_role_actions: [],
                            api_key_role_actions: [],
                            mcp_role_actions: [],
                        },
                    ],
                    total_records: filtered ? 1 : url.searchParams.get("account_status") === "active" ? 16 : 20,
                    account_counts: filtered ? { total: 1, active: 1, inactive: 0 } : { total: 20, active: 16, inactive: 4 },
                    full_access_emails: [],
                },
                headers,
            });
        }
        if (url.pathname.endsWith("/settings/users/00000000001"))
            return route.fulfill({
                json: {
                    user: {
                        uid: "00000000001",
                        firstname: "Range",
                        lastname: "Person",
                        username: "range-person",
                        email: "range@example.invalid",
                        account_status: "active",
                        created_at: "2026-01-01T00:00:00Z",
                        last_signed_in_at: "2026-10-24T01:00:00Z",
                        latest_business_activity_at: activityAt,
                        ...(identitySources === undefined ? {} : { identity_sources: identitySources }),
                        is_admin: false,
                        setting_role_actions: [],
                        api_key_role_actions: [],
                        mcp_role_actions: [],
                    },
                },
                headers,
            });
        if (url.pathname.endsWith("/settings/employee-membership"))
            return route.fulfill({ json: { issuer: "", configured: false, group_uids: [], groups: [] }, headers });
        if (url.pathname.endsWith("/business-activity"))
            return route.fulfill({ json: { records: [], page: 1, limit: 20, has_more: false }, headers });
        return route.fulfill({ json: {}, headers });
    });
    await page.goto(fixture);
    await expect(page.getByText("range@example.invalid", { exact: true })).toBeVisible();
    await expect(page.getByLabel("User ID", { exact: true })).toHaveText("00000000001");
    await expect(page.getByText("@range-person", { exact: true })).toBeVisible();
    return {
        reads,
        fail: () => {
            fail = true;
        },
    };
}

test("range converts local times to UTC, resets page, clears, and reports request errors", async ({ page }) => {
    const state = await setup(page);
    await page.getByPlaceholder("Search...", { exact: true }).fill("Range");
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("query")).toBe("Range");
    await page.getByRole("combobox", { name: "Account status", exact: true }).click();
    await page.getByRole("option", { name: "Active", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("account_status")).toBe("active");
    await page.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    await page.getByLabel("Sign-in from (inclusive)").fill("2026-10-24T10:00");
    await page.getByLabel("Sign-in before (exclusive)").fill("2026-10-24T11:00");
    await page.getByRole("button", { name: "Apply sign-in range", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("last_signed_in_from")).toBe("2026-10-24T01:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("last_signed_in_before")).toBe("2026-10-24T02:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    expect(state.reads.at(-1)?.searchParams.get("query")).toBe("Range");
    expect(state.reads.at(-1)?.searchParams.get("account_status")).toBe("active");
    expect(state.reads.at(-1)?.searchParams.get("exclude_user_uid")).toBe("fixture-admin");
    await page.getByRole("button", { name: "Clear sign-in range", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("last_signed_in_from")).toBe(false);
    await expect(page.getByLabel("Sign-in from (inclusive)")).toHaveValue("");
    await expect(page.getByLabel("Sign-in before (exclusive)")).toHaveValue("");
    await page.getByLabel("Sign-in from (inclusive)").fill("2026-10-24T12:00");
    await page.getByLabel("Sign-in before (exclusive)").fill("2026-10-24T11:00");
    const previous = state.reads.length;
    await page.getByRole("button", { name: "Apply sign-in range", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Enter valid, unambiguous local times");
    expect(state.reads.length).toBe(previous);
    await page.getByLabel("Sign-in before (exclusive)").fill("");
    state.fail();
    await page.getByRole("button", { name: "Apply sign-in range", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Unable to load users");
    expect(state.reads.at(-1)?.searchParams.has("last_signed_in_before")).toBe(false);
});

test("identity source resets pagination and preserves independent filters", async ({ page }) => {
    const state = await setup(page);
    await page.getByPlaceholder("Search...", { exact: true }).fill("Range");
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("query")).toBe("Range");
    await page.getByRole("combobox", { name: "Account status", exact: true }).click();
    await page.getByRole("option", { name: "Active", exact: true }).click();
    await page.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    for (const [label, value] of [
        ["OIDC linked", "oidc"],
        ["SCIM provisioned", "scim"],
        ["All identity sources", "all"],
    ]) {
        await page.getByRole("combobox", { name: "Identity source", exact: true }).click();
        await page.getByRole("option", { name: label, exact: true }).click();
        await expect.poll(() => state.reads.at(-1)?.searchParams.get("identity_provider")).toBe(value);
        expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
        expect(state.reads.at(-1)?.searchParams.get("query")).toBe("Range");
        expect(state.reads.at(-1)?.searchParams.get("account_status")).toBe("active");
        expect(state.reads.at(-1)?.searchParams.get("exclude_user_uid")).toBe("fixture-admin");
    }
});

test.describe("DST validation", () => {
    test.use({ timezoneId: "America/New_York" });
    for (const local of ["2026-03-08T02:30", "2026-11-01T01:30"]) {
        test(`rejects nonexistent or repeated local minute ${local}`, async ({ page }) => {
            const state = await setup(page);
            const previous = state.reads.length;
            await page.getByLabel("Sign-in from (inclusive)").fill(local);
            await page.getByRole("button", { name: "Apply sign-in range", exact: true }).click();
            await expect(page.getByRole("alert")).toContainText("Enter valid, unambiguous local times");
            expect(state.reads.length).toBe(previous);
        });
    }
});

test("business range remains independent, coexists with sign-in, resets page and validates without a request", async ({ page }) => {
    const state = await setup(page);
    await expect(page.getByTitle("2026-10-23T02:30:00.000Z", { exact: true })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Latest business activity", exact: true })).toHaveAttribute("aria-sort", "none");
    await page.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    await page.getByLabel("Business activity from (inclusive)").fill("2026-10-23T11:30");
    await page.getByLabel("Business activity before (exclusive)").fill("2026-10-24T11:30");
    await page.getByRole("button", { name: "Apply business activity range", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("last_business_activity_from")).toBe("2026-10-23T02:30:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("last_business_activity_before")).toBe("2026-10-24T02:30:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    expect(state.reads.at(-1)?.searchParams.has("last_signed_in_from")).toBe(false);
    await page.getByLabel("Sign-in from (inclusive)").fill("2026-10-24T10:00");
    await page.getByRole("button", { name: "Apply sign-in range", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("last_signed_in_from")).toBe("2026-10-24T01:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("last_business_activity_from")).toBe("2026-10-23T02:30:00.000Z");
    await page.getByRole("button", { name: "Clear sign-in range", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("last_signed_in_from")).toBe(false);
    expect(state.reads.at(-1)?.searchParams.has("last_business_activity_before")).toBe(true);
    await page.getByLabel("Business activity before (exclusive)").fill("2026-10-23T11:30");
    const previous = state.reads.length;
    await page.getByRole("button", { name: "Apply business activity range", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Enter valid, unambiguous local times");
    expect(state.reads.length).toBe(previous);
    await page.getByRole("button", { name: "Clear business activity range", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("last_business_activity_from")).toBe(false);
    expect(state.reads.at(-1)?.searchParams.has("last_business_activity_before")).toBe(false);
    await expect(page.getByLabel("Business activity from (inclusive)")).toHaveValue("");
    await expect(page.getByLabel("Business activity before (exclusive)")).toHaveValue("");
    await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const activityAt of [null, "2026-10-23T02:30:00Z"]) {
    test(`list and detail show independent business activity ${activityAt ?? "null"}`, async ({ page }) => {
        await setup(page, activityAt);
        const value = activityAt
            ? page.getByTitle("2026-10-23T02:30:00.000Z", { exact: true })
            : page.getByText("No recorded business activity", { exact: true });
        await expect(value).toBeVisible();
        await expect(page.getByText("No recorded sign-in", { exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "More", exact: true }).click();
        await page.getByRole("menuitem", { name: "User details", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "User details", exact: true });
        await expect(dialog.getByText("Latest business activity", { exact: true })).toBeVisible();
        await expect(
            activityAt
                ? dialog.getByTitle("2026-10-23T02:30:00.000Z", { exact: true })
                : dialog.getByText("No recorded business activity", { exact: true })
        ).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
    });
}

test.describe("business activity DST validation", () => {
    test.use({ timezoneId: "America/New_York" });
    for (const local of ["2026-03-08T02:30", "2026-11-01T01:30"]) {
        test(`rejects ambiguous business activity bound ${local}`, async ({ page }) => {
            const state = await setup(page);
            const previous = state.reads.length;
            await page.getByLabel("Business activity from (inclusive)").fill(local);
            await page.getByRole("button", { name: "Apply business activity range", exact: true }).click();
            await expect(page.getByRole("alert")).toContainText("Enter valid, unambiguous local times");
            expect(state.reads.length).toBe(previous);
        });
    }
});

for (const [sources, label] of [
    [undefined, "Identity source unavailable"],
    [[], "No recorded identity source"],
    [["oidc"], "OIDC linked"],
    [["scim"], "SCIM provisioned"],
    [["oidc", "scim"], "OIDC linked, SCIM provisioned"],
] as const) {
    test(`identity source display in list and detail: ${label}`, async ({ page }) => {
        await setup(page, null, sources ? [...sources] : undefined);
        await expect(page.getByRole("cell", { name: label, exact: true })).toBeVisible();
        await page.getByRole("button", { name: "More", exact: true }).click();
        await page.getByRole("menuitem", { name: "User details", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "User details", exact: true });
        await expect(dialog.getByText(label, { exact: true })).toBeVisible();
        await expect(dialog).not.toContainText("Local authentication");
    });
}

test("login and business time headers sort remotely with keyboard and reset pagination", async ({ page }) => {
    const state = await setup(page);
    await page.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    const login = page.getByRole("columnheader", { name: "Last successful sign-in", exact: true });
    await login.focus();
    await login.press("Enter");
    await expect(login).toHaveAttribute("aria-sort", "ascending");
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("sort_by")).toBe("last_signed_in_at");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    expect(state.reads.at(-1)?.searchParams.get("sort_direction")).toBe("asc");
    await login.press("Space");
    await expect(login).toHaveAttribute("aria-sort", "descending");
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("sort_direction")).toBe("desc");
    const business = page.getByRole("columnheader", { name: "Latest business activity", exact: true });
    await business.click();
    await expect(business).toHaveAttribute("aria-sort", "ascending");
    await expect(login).toHaveAttribute("aria-sort", "none");
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("sort_by")).toBe("latest_business_activity_at");
    expect(state.reads.at(-1)?.searchParams.get("sort_direction")).toBe("asc");
});

test("account summary shows server totals across statuses and hides them after request failure", async ({ page }) => {
    const state = await setup(page);
    const summary = page.getByRole("region", { name: "Account summary", exact: true });
    await expect(summary.getByText("20", { exact: true })).toBeVisible();
    await expect(summary.getByText("16", { exact: true })).toBeVisible();
    await expect(summary.getByText("4", { exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Account status", exact: true }).click();
    await page.getByRole("option", { name: "Active", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("account_status")).toBe("active");
    await expect(summary.getByText("20", { exact: true })).toBeVisible();
    state.fail();
    await page.getByRole("combobox", { name: "Account status", exact: true }).click();
    await page.getByRole("option", { name: "All accounts", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Unable to load users. Refresh to retry.");
    await expect(summary).toHaveCount(0);
});

test("same-user token replacement hides the prior summary before identity hydration and reloads the directory", async ({ page }) => {
    await setup(page);
    const summary = page.getByRole("region", { name: "Account summary", exact: true });
    await expect(summary.getByText("20", { exact: true })).toBeVisible();
    let authReads = 0,
        directoryReads = 0;
    let releaseAuth = () => {},
        releaseDirectory = () => {};
    const authGate = new Promise<void>((resolve) => {
        releaseAuth = resolve;
    });
    const directoryGate = new Promise<void>((resolve) => {
        releaseDirectory = resolve;
    });
    const headers = {
        "access-control-allow-origin": "http://127.0.0.1:4199",
        "access-control-allow-credentials": "true",
        "access-control-allow-headers": "authorization,content-type",
    };
    await page.route(
        (url) => url.port === "5381" && url.pathname.endsWith("/auth/me"),
        async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            authReads++;
            await authGate;
            await route
                .fulfill({
                    headers,
                    json: {
                        user: {
                            uid: "fixture-admin",
                            type: "user",
                            firstname: "Fixture",
                            lastname: "Admin",
                            username: "fixture",
                            is_admin: true,
                            setting_role_actions: ["*"],
                            api_key_role_actions: [],
                            mcp_role_actions: [],
                            created_at: "2026-01-01T00:00:00Z",
                            updated_at: "2026-01-01T00:00:00Z",
                        },
                        bots: [],
                    },
                })
                .catch(() => {});
        }
    );
    await page.route(
        (url) => url.port === "5381" && url.pathname.endsWith("/settings/users/page"),
        async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            directoryReads++;
            await directoryGate;
            await route
                .fulfill({
                    headers,
                    json: { records: [], total_records: 99, account_counts: { total: 99, active: 98, inactive: 1 }, full_access_emails: [] },
                })
                .catch(() => {});
        }
    );
    try {
        await page.evaluate(async () => {
            const authPath = "/src/core/stores/AuthStore.ts",
                apiPath = "/src/core/helpers/Api.ts";
            const [{ default: auth }, { api }] = await Promise.all([import(authPath), import(apiPath)]);
            void auth.getState().updateToken("fixture-summary-next-session", api);
        });
        await expect.poll(() => authReads).toBe(1);
        await expect(summary).toHaveCount(0);
        await expect.poll(() => directoryReads).toBe(1);
        releaseAuth();
        releaseDirectory();
        await expect(summary.getByText("99", { exact: true })).toBeVisible();
    } finally {
        releaseAuth();
        releaseDirectory();
    }
});

test("administrator classification resets page and preserves identity and status filters", async ({ page }) => {
    const state = await setup(page);
    await page.getByPlaceholder("Search...", { exact: true }).fill("Range");
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("query")).toBe("Range");
    await page.getByRole("combobox", { name: "Account status", exact: true }).click();
    await page.getByRole("option", { name: "Active", exact: true }).click();
    await page.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    for (const [label, value] of [
        ["Admin", "admin"],
        ["Other accounts", "non_admin"],
        ["All accounts", "all"],
    ]) {
        await page.getByRole("combobox", { name: "Administrator status", exact: true }).click();
        await page.getByRole("option", { name: label, exact: true }).click();
        await expect.poll(() => state.reads.at(-1)?.searchParams.get("admin_role")).toBe(value);
        expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
        expect(state.reads.at(-1)?.searchParams.get("query")).toBe("Range");
        expect(state.reads.at(-1)?.searchParams.get("account_status")).toBe("active");
        expect(state.reads.at(-1)?.searchParams.get("identity_provider")).toBe("all");
    }
});
