import { test, expect, type Page } from "@playwright/test";
const fixture = "/src/pages/SettingsPage/components/users/group-directory.fixture.html?mode=user";
async function setup(page: Page) {
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
                            account_status: "active",
                            setting_role_actions: [],
                            api_key_role_actions: [],
                            mcp_role_actions: [],
                        },
                    ],
                    total_records: filtered ? 1 : 16,
                    full_access_emails: [],
                },
                headers,
            });
        }
        if (url.pathname.endsWith("/settings/employee-membership"))
            return route.fulfill({ json: { issuer: "", configured: false, group_uids: [], groups: [] }, headers });
        return route.fulfill({ json: {}, headers });
    });
    await page.goto(fixture);
    await expect(page.getByText("range@example.invalid", { exact: true })).toBeVisible();
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
