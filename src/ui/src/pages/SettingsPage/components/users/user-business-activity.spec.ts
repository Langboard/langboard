import { test, expect, type Page } from "@playwright/test";

// Real Chrome UI against the existing native fixture; HTTP responses are contract fixtures.
const fixture = "/src/pages/SettingsPage/components/users/group-directory.fixture.html?mode=user";
async function setup(page: Page, baseURL: string | undefined) {
    if (!baseURL) throw new Error("Business activity fixture requires a Playwright baseURL");
    const frontendOrigin = new URL(baseURL).origin;
    const reads: URL[] = [];
    let failure = 0;
    let empty = false;
    let delay = false;
    let release: (() => void) | undefined;
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
        if (url.pathname.endsWith("/settings/roles"))
            return route.fulfill({ json: { setting_role_actions: ["user_read"], api_key_role_actions: [], mcp_role_actions: [] }, headers });
        if (url.pathname.endsWith("/settings/users/page"))
            return route.fulfill({
                json: { records: [user("00000000001"), user("00000000002")], total_records: 2, full_access_emails: [] },
                headers,
            });
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
        if (target) return route.fulfill({ json: { user: user(target) }, headers });
        return route.fulfill({ json: {}, headers });
    });
    await page.goto(fixture);
    await expect(page.getByText("00000000001@example.invalid", { exact: true })).toBeVisible();
    return {
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
