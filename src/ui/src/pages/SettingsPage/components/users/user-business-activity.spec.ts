import { test, expect, type Page } from "@playwright/test";

// Real Chrome UI against the existing native fixture; HTTP responses are contract fixtures.
const fixture = "/src/pages/SettingsPage/components/users/group-directory.fixture.html?mode=user";
async function setup(page: Page, baseURL: string | undefined, detailOnly = false, withAudit = false) {
    if (!baseURL) throw new Error("Business activity fixture requires a Playwright baseURL");
    const frontendOrigin = new URL(baseURL).origin;
    const reads: URL[] = [];
    const catalog = { reads: 0 };
    const audit = { reads: [] as URL[], failure: 0 };
    let failure = 0;
    let empty = false;
    let delay = false;
    let release: (() => void) | undefined;
    const detail = {
        reads: 0,
        title: "Privileged original detail",
        failure: 0,
        delay: false,
        release: () => {},
        authUID: "fixture-admin",
        authAdmin: true,
        authDelay: false,
        authRelease: () => {},
        authReads: 0,
    };
    const user = (uid: string) => ({
        uid,
        type: "user",
        firstname: uid.endsWith("1") ? "Alpha" : "Beta",
        lastname: "Person",
        username: uid,
        email: `${uid}@example.invalid`,
        is_admin: false,
        activated_at: "2026-01-01T00:00:00Z",
        account_status: "active",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        last_signed_in_at: null,
        latest_business_activity_at: null,
        setting_role_actions: [],
        api_key_role_actions: [],
        mcp_role_actions: [],
    });
    await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.origin === frontendOrigin) return route.continue();
        if (url.port !== "5381") return route.abort();
        const headers = {
            "access-control-allow-origin": frontendOrigin,
            "access-control-allow-credentials": "true",
            "access-control-allow-headers": "*",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (url.pathname.endsWith("/auth/me")) {
            detail.authReads += 1;
            if (detail.authDelay) {
                detail.authDelay = false;
                await new Promise<void>((resolve) => {
                    detail.authRelease = resolve;
                });
            }
            return route.fulfill({
                headers,
                json: {
                    user: {
                        ...user(detail.authUID),
                        is_admin: detail.authAdmin,
                        setting_role_actions: detail.authAdmin ? ["user_read", ...(withAudit ? ["user_audit_read"] : [])] : [],
                    },
                    bots: [],
                },
            });
        }
        if (url.pathname.endsWith("/dashboard/projects")) {
            catalog.reads += 1;
            return route.fulfill({
                json: {
                    projects: [
                        {
                            uid: "00000000003",
                            title: "Readable project",
                            owner_uid: "fixture-admin",
                            project_type: "Other",
                            created_at: "2026-01-01T00:00:00Z",
                            updated_at: "2026-01-01T00:00:00Z",
                        },
                    ],
                    columns: [],
                },
                headers,
            });
        }
        if (url.pathname.endsWith("/settings/roles"))
            return route.fulfill({ json: { setting_role_actions: ["user_read"], api_key_role_actions: [], mcp_role_actions: [] }, headers });
        if (url.pathname.endsWith("/settings/users/page"))
            return route.fulfill({
                json: { records: [user("00000000001"), user("00000000002")], total_records: 2, full_access_emails: [] },
                headers,
            });
        if (url.pathname.endsWith("/sign-in-history")) {
            audit.reads.push(url);
            const currentPage = Number(url.searchParams.get("page"));
            return route.fulfill({
                status: audit.failure || 200,
                headers,
                json: {
                    records: [
                        {
                            event_id: `sign-in-${currentPage}`,
                            occurred_at: "2026-10-31T00:00:00Z",
                            is_success: currentPage > 1,
                            error_code: currentPage > 1 ? null : "invalid_password",
                        },
                    ],
                    page: currentPage,
                    limit: 20,
                    has_more: currentPage === 1,
                },
            });
        }
        if (url.pathname.endsWith("/business-activity")) {
            reads.push(url);
            const status = failure;
            const noRecords = empty;
            if (delay) {
                delay = false;
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            if (status) return route.fulfill({ status, json: {}, headers }).catch(() => {});
            const uid = url.pathname.split("/").at(-2)!;
            const currentPage = Number(url.searchParams.get("page"));
            return route
                .fulfill({
                    json: {
                        records: noRecords
                            ? []
                            : [
                                  {
                                      event_id: `project:${uid}-${currentPage}`,
                                      occurred_at: "2026-10-24T01:00:00Z",
                                      actor_principal_uid: uid,
                                      action: currentPage === 1 ? "card_created" : "wiki_created",
                                      project: { uid: "00000000003", title: "Readable project" },
                                      resource: {
                                          type: currentPage === 1 ? "card" : "wiki",
                                          uid: "00000000004",
                                          title: `${uid} page ${currentPage}`,
                                      },
                                      activity_history: { content: "PRIVATE BODY MUST NOT RENDER" },
                                  },
                              ],
                        page: currentPage,
                        limit: 20,
                        has_more: currentPage === 1,
                    },
                    headers,
                })
                .catch(() => {});
        }
        if (url.pathname.endsWith("/settings/employee-membership"))
            return route.fulfill({ json: { issuer: "", configured: false, group_uids: [], groups: [] }, headers });
        const target = url.pathname.match(/\/settings\/users\/(\w+)$/)?.[1];
        if (target) {
            detail.reads += 1;
            const title = detail.title;
            const status = detail.failure || 200;
            if (detail.delay) {
                detail.delay = false;
                await new Promise<void>((resolve) => {
                    detail.release = resolve;
                });
            }
            return route.fulfill({ status, json: { user: { ...user(target), affiliation: title } }, headers }).catch(() => {});
        }
        return route.fulfill({ json: {}, headers });
    });
    await page.goto(
        detailOnly
            ? "/src/pages/SettingsPage/components/users/user-detail-session.fixture.html"
            : withAudit
              ? fixture.replace("mode=user", "mode=audit")
              : fixture
    );
    if (detailOnly) await expect(page.getByRole("button", { name: "More", exact: true })).toBeVisible();
    else await expect(page.getByText("00000000001@example.invalid", { exact: true })).toBeVisible();
    return {
        detail,
        audit,
        catalog,
        reads,
        fail: (status: number) => {
            failure = status;
        },
        empty: () => {
            empty = true;
        },
        delay: () => {
            delay = true;
        },
        release: () => release?.(),
    };
}
async function open(page: Page, index = 0) {
    await page.getByRole("button", { name: "More", exact: true }).nth(index).click();
    await page.getByRole("menuitem", { name: "User details", exact: true }).click();
    return page.getByRole("region", { name: "Business activity", exact: true });
}

test("ordinary directory read does not request security audit metadata", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL);
    const feed = await open(page);
    await expect(feed.getByText("00000000001 page 1", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("region", { name: "Sign-in security history", exact: true })).toHaveCount(0);
    expect(state.audit.reads).toHaveLength(0);
});

test("separate audit grant shows sign-in pages and applies UTC range filters", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL, false, true);
    await open(page);
    const audit = page.getByRole("region", { name: "Sign-in security history", exact: true });
    await expect(audit.getByRole("cell", { name: "Failed", exact: true })).toBeVisible();
    await audit.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect(audit.getByRole("cell", { name: "Succeeded", exact: true })).toBeVisible();
    expect(state.audit.reads.at(-1)?.searchParams.get("page")).toBe("2");
    await audit.getByLabel("Sign-in outcome", { exact: true }).click();
    await page.getByRole("option", { name: "Failed", exact: true }).click();
    await expect.poll(() => state.audit.reads.at(-1)?.searchParams.get("outcome")).toBe("failure");
    expect(state.audit.reads.at(-1)?.searchParams.get("page")).toBe("1");
    await audit.getByLabel("From (inclusive)").fill("2026-10-31T09:00");
    await audit.getByLabel("Before (exclusive)").fill("2026-10-31T10:00");
    await audit.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect.poll(() => state.audit.reads.at(-1)?.searchParams.get("occurred_from")).toBe("2026-10-31T00:00:00.000Z");
    expect(state.audit.reads.at(-1)?.searchParams.get("occurred_before")).toBe("2026-10-31T01:00:00.000Z");
    const count = state.audit.reads.length;
    await audit.getByLabel("Before (exclusive)").fill("2026-10-31T08:00");
    await audit.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect(audit.getByRole("alert")).toHaveText("Enter a valid start time earlier than the end time.");
    expect(state.audit.reads).toHaveLength(count);
});

test("revoked audit requests remove prior sign-in results", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL, false, true);
    await open(page);
    const audit = page.getByRole("region", { name: "Sign-in security history", exact: true });
    await expect(audit.getByRole("cell", { name: "Failed", exact: true })).toBeVisible();
    state.audit.failure = 403;
    await audit.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(audit.getByRole("alert")).toBeVisible();
    await expect(audit.getByRole("cell", { name: "Failed", exact: true })).toHaveCount(0);
    await expect(audit.getByRole("button", { name: "Next Page", exact: true })).toBeDisabled();
});

test.describe("audit range DST validation", () => {
    test.use({ timezoneId: "America/New_York" });
    for (const localTime of ["2026-03-08T02:30", "2026-11-01T01:30"]) {
        test(`audit range rejects ambiguous local time ${localTime}`, async ({ page, baseURL }) => {
            const state = await setup(page, baseURL, false, true);
            await open(page);
            const audit = page.getByRole("region", { name: "Sign-in security history", exact: true });
            await expect(audit.getByRole("cell", { name: "Failed", exact: true })).toBeVisible();
            const count = state.audit.reads.length;
            await audit.getByLabel("From (inclusive)").fill(localTime);
            await audit.getByRole("button", { name: "Apply filters", exact: true }).click();
            await expect(audit.getByRole("alert")).toHaveText("Enter a valid start time earlier than the end time.");
            expect(state.audit.reads).toHaveLength(count);
        });
    }
});

test("metadata feed paginates within server bounds and preserves independent sign-in details", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL);
    const feed = await open(page);
    await expect(feed.getByText("00000000001 page 1", { exact: true }).first()).toBeVisible();
    await expect(feed.getByRole("button", { name: "Previous Page", exact: true })).toBeDisabled();
    await expect(feed.getByRole("link", { name: "00000000001 page 1", exact: true }).first()).toHaveAttribute(
        "href",
        "/board/00000000003/00000000004"
    );
    await expect(page.getByText("No recorded sign-in", { exact: true }).first()).toBeVisible();
    await expect(feed.getByText("PRIVATE BODY MUST NOT RENDER")).toHaveCount(0);
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect(feed.getByText("00000000001 page 2", { exact: true }).first()).toBeVisible();
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    expect(state.reads.at(-1)?.searchParams.get("limit")).toBe("20");
    await expect(feed.getByRole("button", { name: "Next Page", exact: true })).toBeDisabled();
    await expect(feed.getByRole("link", { name: "00000000001 page 2", exact: true }).first()).toHaveAttribute(
        "href",
        "/board/00000000003/wiki/00000000004"
    );
    await feed.getByRole("button", { name: "Previous Page", exact: true }).click();
    await expect(feed.getByText("00000000001 page 1", { exact: true }).first()).toBeVisible();
});

test("closing a pending page and switching targets cannot show the previous user's response", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL);
    let feed = await open(page);
    await expect(feed.getByText("00000000001 page 1", { exact: true }).first()).toBeVisible();
    state.delay();
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect(feed).toHaveAttribute("aria-busy", "true");
    await expect(feed.getByText("00000000001 page 1", { exact: true })).toHaveCount(0);
    await expect(feed.getByRole("button", { name: "Next Page", exact: true })).toBeDisabled();
    await expect.poll(() => state.reads.length).toBe(2);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.keyboard.press("Escape");
    feed = await open(page, 1);
    await expect(feed.getByText("00000000002 page 1", { exact: true }).first()).toBeVisible();
    state.release();
    await expect(feed.getByText("00000000001 page 2", { exact: true })).toHaveCount(0);
    await expect(feed.getByText("00000000002 page 1", { exact: true }).first()).toBeVisible();
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
});

test("denied and failed requests hide prior metadata; explicit retry can show the empty state", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL);
    const feed = await open(page);
    await expect(feed.getByText("00000000001 page 1", { exact: true }).first()).toBeVisible();
    state.fail(403);
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect(feed.getByRole("alert")).toHaveText("Directory access denied");
    await expect(feed.getByText("00000000001 page 1", { exact: true })).toHaveCount(0);
    await expect(feed.getByRole("button", { name: "Previous Page", exact: true })).toBeDisabled();
    state.fail(500);
    await feed.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(feed.getByRole("alert")).toContainText("Unable to load this user");
    state.fail(0);
    state.empty();
    await feed.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(feed.getByText("No activities", { exact: true })).toBeVisible();
    await expect(feed.getByRole("button", { name: "Next Page", exact: true })).toBeDisabled();
});

async function replaceSession(page: Page) {
    await page.evaluate(async () => {
        const authPath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: auth }, { api }] = await Promise.all([import(authPath), import(apiPath)]);
        await auth.getState().updateToken("fixture-next-session", api);
    });
}

for (const transition of ["account", "session"] as const) {
    test(`user detail clears a retained response on ${transition} replacement`, async ({ page, baseURL }) => {
        const { detail } = await setup(page, baseURL, true);
        if (transition === "session") await replaceSession(page);
        await open(page);
        const dialog = page.getByRole("dialog");
        await expect(dialog.getByText(detail.title, { exact: true })).toBeVisible();
        detail.title = "Current session detail";
        if (transition === "account") detail.authUID = "other-admin";
        await replaceSession(page);
        await expect(dialog.getByText("Privileged original detail", { exact: true })).toHaveCount(0);
        await expect(dialog.getByText("Current session detail", { exact: true })).toBeVisible();
        expect(detail.reads).toBeGreaterThanOrEqual(2);
    });

    test(`user detail rejects a pending response on ${transition} replacement`, async ({ page, baseURL }) => {
        const { detail } = await setup(page, baseURL, true);
        if (transition === "session") await replaceSession(page);
        detail.delay = true;
        await open(page);
        await expect.poll(() => detail.reads).toBe(1);
        detail.title = "Current session detail";
        if (transition === "account") detail.authUID = "other-admin";
        await replaceSession(page);
        detail.release();
        const dialog = page.getByRole("dialog");
        await expect(dialog.getByText("Current session detail", { exact: true })).toBeVisible();
        await expect(dialog.getByText("Privileged original detail", { exact: true })).toHaveCount(0);
        expect(detail.reads).toBeGreaterThanOrEqual(2);
    });
}

for (const status of [401, 403]) {
    test(`user detail clears sensitive content after ${status}`, async ({ page, baseURL }) => {
        const { detail } = await setup(page, baseURL, true);
        await open(page);
        const dialog = page.getByRole("dialog");
        await expect(dialog.getByText(detail.title, { exact: true })).toBeVisible();
        detail.failure = status;
        await dialog.getByRole("button", { name: "Refresh", exact: true }).first().click();
        await expect(dialog.getByText("Privileged original detail", { exact: true })).toHaveCount(0);
        await expect(dialog.getByRole("region", { name: "Business activity", exact: true })).toHaveCount(0);
        await expect(dialog.getByRole("alert")).toBeVisible();
        if (status === 403) {
            detail.failure = 0;
            detail.title = "Authorized refreshed detail";
            await dialog.getByRole("button", { name: "Refresh", exact: true }).first().click();
            await expect(dialog.getByText(detail.title, { exact: true })).toBeVisible();
        }
    });
}

test("user detail invalidates when token replacement begins before identity hydration", async ({ page, baseURL }) => {
    const { detail } = await setup(page, baseURL, true);
    await replaceSession(page);
    await open(page);
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText(detail.title, { exact: true })).toBeVisible();
    detail.authDelay = true;
    detail.title = "Current session detail";
    await page.evaluate(async () => {
        const authPath = "/src/core/stores/AuthStore.ts";
        const apiPath = "/src/core/helpers/Api.ts";
        const [{ default: auth }, { api }] = await Promise.all([import(authPath), import(apiPath)]);
        void auth.getState().updateToken("fixture-next-session", api);
    });
    await expect.poll(() => detail.authReads).toBe(2);
    await expect(dialog.getByText("Privileged original detail", { exact: true })).toHaveCount(0);
    detail.authRelease();
    await expect(dialog.getByText("Current session detail", { exact: true })).toBeVisible();
});

test("identity hydration with revoked administration closes details and rejects the late response", async ({ page, baseURL }) => {
    const { detail } = await setup(page, baseURL);
    await open(page);
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText(detail.title, { exact: true })).toBeVisible();
    detail.delay = true;
    await dialog.getByRole("button", { name: "Refresh", exact: true }).first().click();
    await expect.poll(() => detail.reads).toBe(2);
    detail.authAdmin = false;
    detail.failure = 403;
    await replaceSession(page);
    await expect(dialog).toHaveCount(0);
    detail.release();
    await expect(page.getByText("Privileged original detail", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert")).toContainText("Directory access denied");
});

test("business activity applies UTC bounds across pages and rejects reversed ranges", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL);
    const feed = await open(page);
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect(feed.getByText("00000000001 page 2", { exact: true }).first()).toBeVisible();
    await feed.getByLabel("From (inclusive)").fill("2026-10-31T09:00");
    await feed.getByLabel("Before (exclusive)").fill("2026-10-31T10:00");
    await feed.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("occurred_from")).toBe("2026-10-31T00:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("occurred_before")).toBe("2026-10-31T01:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    expect(state.reads.at(-1)?.searchParams.get("occurred_from")).toBe("2026-10-31T00:00:00.000Z");
    const count = state.reads.length;
    await feed.getByLabel("Before (exclusive)").fill("2026-10-31T08:00");
    await feed.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect(feed.getByRole("alert")).toHaveText("Enter a valid start time earlier than the end time.");
    expect(state.reads).toHaveLength(count);
    await feed.getByLabel("From (inclusive)").fill("");
    await feed.getByLabel("Before (exclusive)").fill("");
    await feed.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("occurred_from")).toBe(false);
    expect(state.reads.at(-1)?.searchParams.has("occurred_before")).toBe(false);
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
});

test("business activity project filter shares the catalog and preserves time bounds across pages", async ({ page, baseURL }) => {
    const state = await setup(page, baseURL);
    const feed = await open(page);
    await expect(feed.getByRole("combobox", { name: "Activity project", exact: true })).toBeVisible();
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    await feed.getByRole("combobox", { name: "Activity project", exact: true }).click();
    await page.getByRole("option", { name: "Readable project", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("project_uid")).toBe("00000000003");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    await feed.getByLabel("From (inclusive)").fill("2026-10-31T09:00");
    await feed.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("occurred_from")).toBe("2026-10-31T00:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("project_uid")).toBe("00000000003");
    await feed.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    expect(state.reads.at(-1)?.searchParams.get("project_uid")).toBe("00000000003");
    await feed.getByRole("combobox", { name: "Activity project", exact: true }).click();
    await page.getByRole("option", { name: "All projects", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("project_uid")).toBe(false);
    expect(state.reads.at(-1)?.searchParams.get("occurred_from")).toBe("2026-10-31T00:00:00.000Z");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    expect(state.catalog.reads).toBe(1);
});
