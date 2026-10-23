import { test, expect, type Page } from "@playwright/test";

const fixture = "/src/pages/SettingsPage/components/users/group-directory.fixture.html";
const groups = [
    {
        uid: "00000000001",
        display_name: "Alpha",
        source: "scim",
        issuer: "https://issuer.example",
        external_id: "alpha",
        source_status: "current_authority",
        managed: true,
        read_only: true,
        abilities: { read_members: true, update: false, delete: false, manage_members: false },
    },
    {
        uid: "00000000002",
        display_name: "Beta",
        source: "scim",
        issuer: "https://other.example",
        external_id: "beta",
        source_status: "other_authority",
        managed: true,
        read_only: true,
        abilities: { read_members: true, update: false, delete: false, manage_members: false },
    },
];
async function setup(page: Page, mode = "group", shell = false) {
    const reads: string[] = [];
    const writes: string[] = [];
    let failGroups = 0;
    let release: (() => void) | undefined;
    let delay = false;
    let delayGroups = false;
    await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.port === "4187") return route.continue();
        if (url.port !== "5381") return route.abort();
        const path = url.pathname;
        if (route.request().method() === "OPTIONS")
            return route.fulfill({
                status: 204,
                headers: {
                    "access-control-allow-origin": "http://127.0.0.1:4187",
                    "access-control-allow-credentials": "true",
                    "access-control-allow-headers": "*",
                },
            });
        if (path.endsWith("/settings/roles"))
            return route.fulfill({
                json: {
                    setting_role_actions: mode === "group" ? ["group_read"] : mode === "user" ? ["user_read"] : [],
                    api_key_role_actions: [],
                    mcp_role_actions: [],
                },
                headers: { "access-control-allow-origin": "http://127.0.0.1:4187", "access-control-allow-credentials": "true" },
            });
        if (route.request().method() !== "GET") writes.push(path);
        reads.push(`${path}${url.search}`);
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4187", "access-control-allow-credentials": "true" };
        if (path.endsWith("/settings/groups")) {
            if (delayGroups) {
                delayGroups = false;
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            if (failGroups) return route.fulfill({ status: failGroups, json: {}, headers });
            const query = url.searchParams.get("query") || "";
            const records = query ? groups.filter((group) => group.display_name.toLowerCase().includes(query.toLowerCase())) : groups;
            return route.fulfill({
                json: {
                    records: url.searchParams.get("page") === "2" ? [{ ...groups[1], display_name: "Page two" }] : records,
                    total_records: query ? records.length : 16,
                },
                headers,
            });
        }
        if (path.endsWith("/members")) {
            if (delay && path.includes(groups[0].uid)) {
                delay = false;
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            const beta = path.includes(groups[1].uid);
            const search = url.searchParams.get("query");
            const records =
                search === "nobody"
                    ? []
                    : ["active", "inactive", "deleted"].map((account_status, index) => ({
                          uid: `member-${index}`,
                          firstname: account_status === "deleted" ? "" : beta ? "Beta member" : "Alpha member",
                          lastname: "",
                          username: account_status === "deleted" ? "" : `reader-${index}`,
                          account_status,
                      }));
            return route
                .fulfill({ json: { records, total_records: search ? records.length : 16, group: beta ? groups[1] : groups[0] }, headers })
                .catch(() => {});
        }
        if (path.endsWith("/settings/users/page")) return route.fulfill({ json: { records: [], total_records: 0, full_access_emails: [] }, headers });
        if (path.endsWith("/settings/employee-membership"))
            return route.fulfill({ json: { issuer: "", configured: false, group_uids: [], groups: [] }, headers });
        return route.fulfill({ json: {}, headers });
    });
    await page.goto(`${fixture}?mode=${mode}${shell ? "&shell=1" : ""}`);
    if (!shell) await expect(page.getByRole("button", { name: "Revoke permissions" })).toBeVisible();
    return {
        reads,
        writes,
        delayGroups: () => {
            delayGroups = true;
        },
        fail: (status: number) => {
            failGroups = status;
        },
        delay: () => {
            delay = true;
        },
        release: () => release?.(),
    };
}

for (const mode of ["group", "user", "create", "none", "nonadmin"]) {
    test(`native tabs isolate reads for ${mode} permissions`, async ({ page }) => {
        const state = await setup(page, mode);
        if (mode === "group") {
            await expect(page.getByRole("tab", { name: "Groups", exact: true })).toBeVisible();
            await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
            expect(state.reads.some((path) => path.includes("/users") || path.includes("employee-membership"))).toBe(false);
            await expect(page.getByRole("tab", { name: "Users", exact: true })).toHaveCount(0);
        } else if (mode === "user") {
            await expect(page.getByRole("tab", { name: "Users", exact: true })).toBeVisible();
            await expect.poll(() => state.reads.filter((path) => path.includes("/users/page")).length).toBe(1);
            await expect.poll(() => state.reads.filter((path) => path.includes("employee-membership")).length).toBe(1);
            expect(state.reads.some((path) => path.includes("/settings/groups"))).toBe(false);
            await expect(page.getByRole("tab", { name: "Groups", exact: true })).toHaveCount(0);
        } else if (mode === "create") {
            await expect(page.getByRole("tab", { name: "Users", exact: true })).toBeVisible();
            await expect(page.getByRole("button", { name: "Add new", exact: true })).toBeVisible();
            expect(state.reads).toEqual([]);
        } else {
            await expect(page.getByRole("alert")).toHaveText("Directory access denied");
            expect(state.reads).toEqual([]);
        }
        expect(state.writes).toEqual([]);
    });
}

test("group and member bounded search, pagination, source, statuses, and read-only controls", async ({ page }) => {
    const state = await setup(page);
    await expect(page.getByText("SCIM · Current authority")).toBeVisible();
    await expect(page.getByText("SCIM · Other authority")).toBeVisible();
    await expect(page.getByText("Read only · Externally managed")).toHaveCount(2);
    const directory = page.getByRole("region", { name: "Groups", exact: true });
    await directory.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect(page.getByRole("button", { name: "Page two", exact: true })).toBeVisible();
    await page.getByLabel("Search groups", { exact: true }).fill("Alpha");
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    const members = page.getByRole("region", { name: "Members: Alpha", exact: true });
    await expect(members.getByText("Active", { exact: true })).toBeVisible();
    await expect(members.getByText("Inactive", { exact: true })).toBeVisible();
    await expect(members.getByText("Deleted", { exact: true })).toBeVisible();
    await members.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.some((path) => path.includes("/members?page=2&limit=15"))).toBe(true);
    await page.getByLabel("Search members", { exact: true }).fill("nobody");
    await expect(members.getByText("No data available", { exact: true })).toBeVisible();
    expect(state.reads.some((path) => path.includes("/members?page=1&limit=15&query=nobody"))).toBe(true);
    expect(await page.getByRole("button", { name: /^(Create|Edit|Delete|Save)$/ }).count()).toBe(0);
    expect(state.writes).toEqual([]);
});

test("selection resets member page/search and rejects late previous selection response; revocation clears privileged data", async ({ page }) => {
    const state = await setup(page);
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(2);
    state.delay();
    await page.getByLabel("Search members", { exact: true }).fill("slow");
    await expect.poll(() => state.reads.some((path) => path.includes("query=slow"))).toBe(true);
    await page.getByRole("button", { name: "Beta", exact: true }).click();
    await expect(page.getByText("Beta member", { exact: true })).toHaveCount(2);
    await expect(page.getByLabel("Search members", { exact: true })).toHaveValue("");
    state.release();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Revoke permissions" }).click();
    await expect(page.getByRole("alert")).toHaveText("Directory access denied");
    await expect(page.getByText("Beta member", { exact: true })).toHaveCount(0);
    expect(state.writes).toEqual([]);
});

test("failed and forbidden group reads hide stale results and retry", async ({ page }) => {
    const state = await setup(page);
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    state.fail(500);
    await page.getByLabel("Search groups", { exact: true }).fill("failure");
    await expect(page.getByRole("alert")).toContainText("Could not load settings");
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toHaveCount(0);
    state.fail(403);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Directory access denied");
    state.fail(0);
    await page.getByLabel("Search groups", { exact: true }).fill("");
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
});

test("both permissions fetch only the selected tab", async ({ page }) => {
    const state = await setup(page, "both");
    await expect.poll(() => state.reads.some((path) => path.includes("/users/page"))).toBe(true);
    expect(state.reads.some((path) => path.includes("/settings/groups"))).toBe(false);
    await page.getByRole("tab", { name: "Groups", exact: true }).click();
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
    const previous = state.reads.filter((path) => path.includes("/users/page")).length;
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(2);
    expect(state.reads.filter((path) => path.includes("/users/page"))).toHaveLength(previous);
});

for (const mode of ["group", "user"]) {
    test(`settings route and native menu admit ${mode}-only admins`, async ({ page }) => {
        const state = await setup(page, mode, true);
        await expect(
            page.getByRole("navigation", { name: "Settings", exact: true }).getByRole("button", { name: "Users", exact: true })
        ).toBeVisible();
        await expect(page.getByRole("tab", { name: mode === "group" ? "Groups" : "Users", exact: true })).toBeVisible();
        if (mode === "group") {
            await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
            expect(state.reads.some((path) => path.includes("/users") || path.includes("employee-membership"))).toBe(false);
        } else {
            await expect.poll(() => state.reads.some((path) => path.includes("/users/page"))).toBe(true);
            expect(state.reads.some((path) => path.includes("/settings/groups"))).toBe(false);
        }
    });
}

test("desktop and narrow native directory layout", async ({ page }) => {
    await setup(page);
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(2);
    await page.screenshot({ path: "/private/tmp/group-directory-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(2);
    await expect(page.getByLabel("Search groups", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: "/private/tmp/group-directory-mobile.png", fullPage: true });
});

test("account switch clears previous group selection before the next account read resolves", async ({ page }) => {
    const state = await setup(page);
    await page.getByRole("button", { name: "Alpha", exact: true }).click();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(2);
    state.delayGroups();
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByText("Alpha member", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toHaveCount(0);
    await expect.poll(() => state.reads.filter((path) => /\/settings\/groups\?/.test(path)).length).toBe(2);
    state.release();
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Members: Alpha", exact: true })).toHaveCount(0);
});

test("late group search response cannot replace newer results", async ({ page }) => {
    const state = await setup(page);
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toBeVisible();
    state.delayGroups();
    await page.getByLabel("Search groups", { exact: true }).fill("Alpha");
    await expect.poll(() => state.reads.some((path) => path.includes("query=Alpha"))).toBe(true);
    await page.getByLabel("Search groups", { exact: true }).fill("Beta");
    await expect(page.getByRole("button", { name: "Beta", exact: true })).toBeVisible();
    state.release();
    await expect(page.getByRole("button", { name: "Alpha", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Beta", exact: true })).toBeVisible();
});
