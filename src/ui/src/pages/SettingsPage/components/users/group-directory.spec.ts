import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const fixture = "/src/pages/SettingsPage/components/users/group-directory.fixture.html";
const locales = ["en-US", "ko-KR", "ja-JP", "zh-CN", "en-US"];
const privateMembershipDetail = "PRIVATE membership issuer diagnostic /srv/private/scim.json";
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
async function setup(page: Page, mode = "group", shell = false, membershipStatus?: number) {
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
        if (path.endsWith("/settings/employee-membership")) {
            if (route.request().method() === "PUT" && membershipStatus)
                return route.fulfill({ status: membershipStatus, json: { detail: privateMembershipDetail }, headers });
            return route.fulfill({
                json: {
                    issuer: membershipStatus === undefined ? "" : "https://issuer.example",
                    configured: membershipStatus !== undefined,
                    group_uids: [],
                    groups: [],
                },
                headers,
            });
        }
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

for (const status of [0, 503])
    test(`membership PUT ${status || "success"} status follows locale changes without reload`, async ({ page }, testInfo) => {
        const state = await setup(page, "user", false, status);
        const membership = page.locator("section[aria-labelledby=employee-membership-title]");
        await membership.getByRole("button", { name: "Save", exact: true }).click();
        const key = status ? "Membership settings failed" : "Membership settings saved";
        for (const locale of locales) {
            const labels = JSON.parse(readFileSync(new URL(`../../../../assets/locales/${locale}/settings.json`, import.meta.url), "utf8"));
            await page.evaluate(async (locale) => {
                const modulePath = "/src/i18n.ts";
                await (await import(/* @vite-ignore */ modulePath)).default.changeLanguage(locale);
            }, locale);
            await expect.soft(membership.getByRole("status")).toHaveText(labels[key], { timeout: 1500 });
            await expect(page.locator("body")).not.toContainText(privateMembershipDetail);
            await expect(membership).toContainText(labels["Employee membership"]);
        }
        expect(state.writes.filter((path) => path.endsWith("/settings/employee-membership"))).toHaveLength(1);
        expect(state.reads.filter((path) => path.endsWith("/settings/employee-membership"))).toHaveLength(2);
        await page.setViewportSize({ width: 390, height: 844 });
        expect(await membership.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath("membership-locale-status.png"), fullPage: true });
    });

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

// Native Chrome UI; HTTP directory responses below are fixtures, not backend proof.
async function setupManagement(page: Page, mode = "manage", source = "local") {
    let group = {
        ...groups[0],
        display_name: "Local team",
        source,
        managed: false,
        read_only: false,
        revision: 1,
        abilities: { read_members: true, update: true, delete: true, manage_members: true },
    };
    let present = true;
    let memberPresent = false;
    let failure = 0;
    let release: (() => void) | undefined;
    let delay = false;
    const writes: { method: string; path: string; body: Record<string, unknown> }[] = [];
    const reads: string[] = [];
    await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.port === "4187") return route.continue();
        if (url.port !== "5381") return route.abort();
        const headers = {
            "access-control-allow-origin": "http://127.0.0.1:4187",
            "access-control-allow-credentials": "true",
            "access-control-allow-headers": "*",
            "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
        };
        const method = route.request().method();
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        const path = url.pathname;
        if (method !== "GET") {
            const body = route.request().postDataJSON();
            writes.push({ method, path, body });
            if (delay) {
                delay = false;
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            if (failure) {
                if (failure === 409) group = { ...group, revision: group.revision + 1 };
                const status = failure;
                failure = 0;
                return route.fulfill({ status, json: {}, headers }).catch(() => {});
            }
            if (path.endsWith("/members")) memberPresent = method === "POST";
            else if (method === "DELETE") present = false;
            else {
                present = true;
                group = { ...group, display_name: body.display_name };
            }
            group = { ...group, revision: group.revision + 1 };
            return route
                .fulfill({ json: method === "DELETE" && !path.endsWith("/members") ? { deleted: true, group_uid: group.uid } : { group }, headers })
                .catch(() => {});
        }
        reads.push(path + url.search);
        const candidate = { uid: "candidate-1", firstname: "Candidate", lastname: "One", username: "candidate", account_status: "active" };
        const json = path.endsWith("/candidates")
            ? { records: [candidate], total_records: 1 }
            : path.endsWith("/history")
              ? {
                    records: [
                        {
                            uid: "audit-1",
                            actor_uid: "admin-audit",
                            action: "rename",
                            group_uid: group.uid,
                            revision: group.revision,
                            changes: { display_name: { before: "Original team", after: group.display_name } },
                            created_at: "2026-10-24T00:00:00Z",
                        },
                    ],
                    total_records: 1,
                }
              : path.endsWith("/members")
                ? { group, records: memberPresent ? [candidate] : [], total_records: memberPresent ? 1 : 0 }
                : path.endsWith("/settings/groups")
                  ? { records: present ? [group] : [], total_records: present ? 1 : 0 }
                  : {};
        return route.fulfill({ json, headers });
    });
    await page.goto(`${fixture}?mode=${mode}`);
    return {
        reads,
        writes,
        fail: (status: number) => {
            failure = status;
        },
        delay: () => {
            delay = true;
        },
        release: () => release?.(),
    };
}

async function openLocal(page: Page) {
    await page.getByRole("button", { name: "Local team", exact: true }).click();
    await expect(page.getByRole("button", { name: "Rename group", exact: true })).toBeVisible();
}

test("native management create rename explicit membership deltas history and deletion", async ({ page }) => {
    const state = await setupManagement(page);
    expect(state.reads.some((path) => path.includes("/candidates"))).toBe(false);
    await page.getByRole("button", { name: "Create group", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("Group name", { exact: true })).toBeFocused();
    await dialog.getByLabel("Group name", { exact: true }).fill("Created team");
    await dialog.getByRole("button", { name: "Create group", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByRole("button", { name: "Created team", exact: true }).click();
    await page.getByRole("button", { name: "Rename group", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("Group name", { exact: true }).fill("Renamed team");
    await dialog.getByRole("button", { name: "Rename group", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Renamed team", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Add members", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("candidate-1", { exact: true }).check();
    await dialog.getByRole("button", { name: "Add members", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByRole("button", { name: "Group history", exact: true }).click();
    await expect(page.getByText("admin-audit", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Group history", exact: true }).getByText("Rename group", { exact: true })).toBeVisible();
    await expect(page.getByText("Group name: Original team → Renamed team", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Remove member", exact: true }).click();
    dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Confirm this destructive change. It cannot be undone.")).toBeVisible();
    await dialog.getByRole("button", { name: "Remove member", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove member", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Delete group", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete group", exact: true }).click();
    await expect(page.getByRole("button", { name: "Renamed team", exact: true })).toHaveCount(0);
    expect(state.writes.map((write) => write.method)).toEqual(["POST", "PUT", "POST", "DELETE", "DELETE"]);
    expect(state.writes[0].body).toEqual({ display_name: "Created team" });
    expect(state.writes[2].body).toEqual({ expected_revision: 3, user_uids: ["candidate-1"] });
    expect(state.writes[3].body).toEqual({ expected_revision: 4, user_uids: ["candidate-1"] });
    expect(state.writes[4].body).toEqual({ expected_revision: 5 });
});

for (const status of [400, 403, 409, 500]) {
    test(`management ${status} preserves input and never replays automatically`, async ({ page }) => {
        const state = await setupManagement(page);
        await openLocal(page);
        state.fail(status);
        await page.getByRole("button", { name: "Rename group", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByLabel("Group name", { exact: true }).fill("Useful draft");
        await dialog.getByRole("button", { name: "Rename group", exact: true }).click();
        await expect(dialog.getByRole("alert")).toBeVisible();
        await expect(dialog.getByLabel("Group name", { exact: true })).toHaveValue("Useful draft");
        expect(state.writes).toHaveLength(1);
        if (status === 409) {
            await expect(dialog.getByText("Local team · Revision: 2", { exact: true })).toBeVisible();
            await dialog.getByRole("button", { name: "Rename group", exact: true }).click();
            await expect(dialog).toHaveCount(0);
            expect(state.writes[1].body).toEqual({ display_name: "Useful draft", expected_revision: 2 });
        }
    });
}

for (const source of ["scim", "legacy_unresolved"]) {
    test(`permissive abilities cannot make ${source} writable`, async ({ page }) => {
        const state = await setupManagement(page, "manage", source);
        await page.getByRole("button", { name: "Local team", exact: true }).click();
        await expect(page.getByRole("region", { name: "Members: Local team", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Rename group", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Add members", exact: true })).toHaveCount(0);
        expect(state.writes).toEqual([]);
    });
}

test("management authority alone cannot render or fetch directory", async ({ page }) => {
    const state = await setupManagement(page, "manageonly");
    await expect(page.getByRole("alert")).toHaveText("Directory access denied");
    expect(state.reads).toEqual([]);
});

for (const next of ["Switch account", "Revoke permissions", "selection"]) {
    test(`pending mutation cannot repopulate after ${next}`, async ({ page }) => {
        const state = await setupManagement(page);
        await openLocal(page);
        state.delay();
        await page.getByRole("button", { name: "Rename group", exact: true }).click();
        await page.getByRole("dialog").getByLabel("Group name", { exact: true }).fill("Late response");
        await page.getByRole("dialog").getByRole("button", { name: "Rename group", exact: true }).click();
        await expect.poll(() => state.writes.length).toBe(1);
        await expect(page.getByRole("dialog").getByRole("button", { name: "Saving group change…", exact: true })).toBeDisabled();
        // A modal traps user focus; account/permission changes arrive independently in production.
        if (next === "selection")
            await page.getByLabel("Search groups", { exact: true }).evaluate((input: HTMLInputElement) => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
                setter.call(input, "other");
                input.dispatchEvent(new Event("input", { bubbles: true }));
            });
        else await page.getByRole("button", { name: next, exact: true, includeHidden: true }).evaluate((button: HTMLButtonElement) => button.click());
        await expect(page.getByRole("dialog")).toHaveCount(0);
        state.release();
        await expect(page.getByRole("button", { name: "Late response", exact: true })).toHaveCount(0);
    });
}

test("narrow management dialog keeps native focus and fits viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await setupManagement(page);
    await page.getByRole("button", { name: "Create group", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("Group name", { exact: true })).toBeFocused();
    await expect(dialog.locator("..")).toHaveCSS("opacity", "1");
    await page.screenshot({ path: "/private/tmp/group-management-dialog-mobile.png", fullPage: true });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Create group", exact: true })).toBeFocused();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: "/private/tmp/group-management-mobile.png", fullPage: true });
});
