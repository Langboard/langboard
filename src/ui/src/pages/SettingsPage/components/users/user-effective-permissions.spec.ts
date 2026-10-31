import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page, baseURL: string | undefined) {
    const origin = new URL(baseURL!).origin;
    const reads: URL[] = [],
        errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const searchReads: URL[] = [];
    const state = {
        authUID: "fixture-admin",
        cardTitle: "Readable shared card",
        searchDelay: false,
        searchRelease: () => {},
        allowed: true,
        updateAllowed: false,
        failure: 0,
        catalogFailure: 0,
        empty: false,
        delay: false,
        cardFailure: 0,
        cardEmpty: false,
        release: () => {},
    };
    const user = (uid: string) => ({
        uid,
        type: "user",
        firstname: "Person",
        lastname: uid,
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
        if (url.origin === origin) return route.continue();
        if (url.port !== "5381") return route.abort();
        const headers = { "access-control-allow-origin": origin, "access-control-allow-credentials": "true", "access-control-allow-headers": "*" };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (url.pathname.endsWith("/dashboard/projects"))
            return route.fulfill({
                status: state.catalogFailure || 200,
                headers,
                json: {
                    projects: state.empty
                        ? []
                        : ["00000000003", "00000000004"].map((uid, index) => ({
                              uid,
                              title: `Readable board ${index + 1}`,
                              owner_uid: "fixture-admin",
                              project_type: "Other",
                              created_at: "2026-01-01T00:00:00Z",
                              updated_at: "2026-01-01T00:00:00Z",
                          })),
                    columns: [],
                },
            });
        if (url.pathname.endsWith("/auth/me"))
            return route.fulfill({
                headers,
                json: { user: { ...user(state.authUID), is_admin: true, setting_role_actions: ["user_read"] }, bots: [] },
            });
        if (url.pathname.endsWith("/cards/context")) {
            searchReads.push(url);
            const title = state.cardTitle;
            if (state.searchDelay) {
                state.searchDelay = false;
                await new Promise<void>((resolve) => {
                    state.searchRelease = resolve;
                });
            }
            return route
                .fulfill({
                    status: state.cardFailure || 200,
                    headers,
                    json: {
                        cards: state.cardEmpty
                            ? []
                            : [
                                  { uid: "00000000006", title, project_column_name: "Doing" },
                                  { uid: "00000000007", title: "Readable internal card", project_column_name: "Doing" },
                              ],
                    },
                })
                .catch(() => {});
        }
        if (url.pathname.endsWith("/settings/roles"))
            return route.fulfill({ json: { setting_role_actions: ["user_read"], api_key_role_actions: [], mcp_role_actions: [] }, headers });
        if (url.pathname.endsWith("/settings/users/page"))
            return route.fulfill({
                json: { records: [user("00000000001"), user("00000000002")], total_records: 2, full_access_emails: [] },
                headers,
            });
        if (url.pathname.endsWith("/business-activity"))
            return route.fulfill({ json: { records: [], page: 1, limit: 20, has_more: false }, headers });
        if (url.pathname.endsWith("/effective-permissions")) {
            reads.push(url);
            const status = state.failure,
                grant = url.searchParams.get("action") === "card_update" ? state.updateAllowed : state.allowed;
            if (state.delay) {
                state.delay = false;
                await new Promise<void>((resolve) => {
                    state.release = resolve;
                });
            }
            const projectUID = url.searchParams.get("project_uid")!;
            return route
                .fulfill({
                    status: status || 200,
                    headers,
                    json: {
                        inspection: {
                            user_uid: url.pathname.split("/").at(-2),
                            resource: {
                                type: url.searchParams.has("card_uid") ? "card" : "project",
                                uid: url.searchParams.get("card_uid") || projectUID,
                            },
                            action:
                                url.searchParams.get("action") === "card_update"
                                    ? "card.update"
                                    : url.searchParams.has("card_uid")
                                      ? "card.read"
                                      : "read",
                            allowed: grant,
                            sources: grant
                                ? url.searchParams.get("card_uid") === "00000000007"
                                    ? [{ type: "project_owner" }, { type: "native_card_visibility" }]
                                    : projectUID.endsWith("3")
                                      ? [{ type: "direct_project_role", uid: "00000000005" }]
                                      : [{ type: "project_owner" }, { type: "administrator_project_membership" }]
                                : [],
                            reason: grant ? null : "native_project_read_scope_denied",
                            channel: "api",
                        },
                    },
                })
                .catch(() => {});
        }
        if (url.pathname.endsWith("/settings/employee-membership"))
            return route.fulfill({ json: { issuer: "", configured: false, group_uids: [], groups: [] }, headers });
        const target = url.pathname.match(/\/settings\/users\/(\w+)$/)?.[1];
        if (target) return route.fulfill({ json: { user: user(target) }, headers });
        return route.fulfill({ json: {}, headers });
    });
    await page.goto("/src/pages/SettingsPage/components/users/group-directory.fixture.html?mode=user");
    await expect(page.getByText("00000000001@example.invalid", { exact: true })).toBeVisible();
    return { state, reads, searchReads, errors };
}
async function open(page: Page, index = 0) {
    await page.getByRole("button", { name: "More", exact: true }).nth(index).click();
    await page.getByRole("menuitem", { name: "User details", exact: true }).click();
    return page.getByRole("region", { name: "Effective board read permission", exact: true });
}
async function choose(page: Page, board = 1) {
    await page.getByRole("combobox", { name: "Select a readable board", exact: true }).click();
    await page.getByRole("option", { name: `Readable board ${board}`, exact: true }).click();
}

test("explicit readable selection shows allowed, denied and translated source explanations", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL),
        panel = await open(page);
    await expect(panel.getByText("Select a board to inspect this user’s current read access.")).toBeVisible();
    expect(reads).toHaveLength(0);
    await choose(page);
    await expect(panel.getByText("Direct board role", { exact: true })).toBeVisible();
    await expect(panel.getByText("Board read allowed", { exact: true })).toBeVisible();
    expect(reads[0].searchParams.get("project_uid")).toBe("00000000003");
    await choose(page, 2);
    await expect(panel.getByText("Board ownership", { exact: true })).toBeVisible();
    await expect(panel.getByText("Administrator with board membership", { exact: true })).toBeVisible();
    state.allowed = false;
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(panel.getByText("Board read denied", { exact: true })).toBeVisible();
    await expect(panel.getByText("Board ownership", { exact: true })).toHaveCount(0);
    await expect(panel.getByText("The current account, membership or read grant does not satisfy board read access.")).toBeVisible();
    expect(errors).toEqual([]);
});

test("pending board switch suppresses stale grant", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL),
        panel = await open(page);
    state.delay = true;
    await choose(page);
    await expect(panel).toHaveAttribute("aria-busy", "true");
    await expect.poll(() => reads.length).toBe(1);
    await choose(page, 2);
    await expect(panel.getByText("Board ownership", { exact: true })).toBeVisible();
    state.release();
    await expect(panel.getByText("Direct board role", { exact: true })).toHaveCount(0);
    await expect(panel.getByText("Board ownership", { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});

test("closing pending inspection and switching user resets selection", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL);
    await open(page);
    state.delay = true;
    await choose(page);
    await expect.poll(() => reads.length).toBe(1);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.keyboard.press("Escape");
    const panel = await open(page, 1);
    await expect(panel.getByText("Select a board to inspect this user’s current read access.")).toBeVisible();
    state.release();
    await expect(panel.getByText("Board read allowed", { exact: true })).toHaveCount(0);
    await choose(page, 2);
    await expect(panel.getByText("Board ownership", { exact: true })).toBeVisible();
    expect(reads.at(-1)?.pathname).toContain("00000000002/effective-permissions");
    expect(errors).toEqual([]);
});

for (const status of [403, 404, 500])
    test(`inspection ${status} clears grant and retry recovers`, async ({ page, baseURL }) => {
        const { state, errors } = await setup(page, baseURL),
            panel = await open(page);
        await choose(page);
        await expect(panel.getByText("Board read allowed", { exact: true })).toBeVisible();
        state.failure = status;
        await panel.getByRole("button", { name: "Refresh", exact: true }).click();
        await expect(panel.getByRole("alert")).toContainText(
            status === 403
                ? "Directory access denied"
                : status === 404
                  ? "This user, board or card is no longer available to inspect."
                  : "Unable to load this user"
        );
        await expect(panel.getByText("Direct board role", { exact: true })).toHaveCount(0);
        state.failure = 0;
        await panel.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(panel.getByText("Board read allowed", { exact: true })).toBeVisible();
        expect(errors).toEqual([]);
    });

test("catalog retry and empty state never inspect a board", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL);
    state.catalogFailure = 500;
    const panel = await open(page);
    await expect(panel.getByRole("alert")).toContainText("Could not load projects");
    state.catalogFailure = 0;
    state.empty = true;
    await panel.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(panel.getByText("No readable boards available", { exact: true })).toBeVisible();
    await expect(panel.getByRole("combobox")).toHaveCount(0);
    expect(reads).toHaveLength(0);
    expect(errors).toEqual([]);
});

async function chooseCard(page: Page, name = "Readable shared card") {
    await page.getByRole("textbox", { name: "Search readable cards", exact: true }).fill("Readable");
    await page.getByRole("combobox", { name: "Select cards", exact: true }).click();
    await page.getByRole("option", { name, exact: true }).click();
}

async function chooseAction(page: Page, action: "Read" | "Card Update") {
    await page.getByRole("combobox", { name: "Permission action", exact: true }).click();
    await page.getByRole("option", { name: action, exact: true }).click();
}

test("card update inspection distinguishes read permission and rechecks a revoked grant", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL),
        panel = await open(page);
    await choose(page);
    await chooseCard(page);
    await expect(panel.getByText("Card read allowed", { exact: true })).toBeVisible();
    await chooseAction(page, "Card Update");
    await expect(panel.getByText("Card update denied", { exact: true })).toBeVisible();
    expect(reads.at(-1)?.searchParams.get("action")).toBe("card_update");
    state.updateAllowed = true;
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(panel.getByText("Card update allowed", { exact: true })).toBeVisible();
    state.updateAllowed = false;
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(panel.getByText("Card update denied", { exact: true })).toBeVisible();
    await expect(panel.getByText("Card update allowed", { exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
});

test("switching permission action rejects a delayed previous grant", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL),
        panel = await open(page);
    await choose(page);
    await chooseCard(page);
    state.updateAllowed = true;
    state.delay = true;
    await chooseAction(page, "Card Update");
    await expect.poll(() => reads.some((url) => url.searchParams.get("action") === "card_update")).toBe(true);
    await chooseAction(page, "Read");
    await expect(panel.getByText("Card read allowed", { exact: true })).toBeVisible();
    state.release();
    await expect(panel.getByText("Card update allowed", { exact: true })).toHaveCount(0);
    await expect(panel.getByText("Card read allowed", { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});

test("card selector sends card identity, denial and board reset", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL),
        panel = await open(page);
    await choose(page);
    await chooseCard(page);
    await expect(panel.getByText("Card read allowed", { exact: true })).toBeVisible();
    expect(reads.at(-1)?.searchParams.get("card_uid")).toBe("00000000006");
    state.allowed = false;
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(panel.getByText("Card read denied", { exact: true })).toBeVisible();
    await expect(panel.getByText("Direct board role", { exact: true })).toHaveCount(0);
    state.allowed = true;
    await choose(page, 2);
    await expect(panel.getByText("Card read denied", { exact: true })).toHaveCount(0);
    await expect(panel.getByRole("textbox", { name: "Search readable cards", exact: true })).toHaveValue("");
    await expect(panel.getByText("Board read allowed", { exact: true })).toBeVisible();
    expect(reads.at(-1)?.searchParams.has("card_uid")).toBe(false);
    expect(errors).toEqual([]);
});

test("pending card switch and inspection error suppress previous grants", async ({ page, baseURL }) => {
    const { state, reads, errors } = await setup(page, baseURL),
        panel = await open(page);
    await choose(page);
    state.delay = true;
    await chooseCard(page);
    await expect.poll(() => reads.some((url) => url.searchParams.get("card_uid") === "00000000006")).toBe(true);
    await page.getByRole("combobox", { name: "Select cards", exact: true }).click();
    await page.getByRole("option", { name: "Readable internal card", exact: true }).click();
    await expect(panel.getByText("Card read allowed", { exact: true })).toBeVisible();
    state.release();
    await expect(panel.getByText("Board ownership", { exact: true })).toBeVisible();
    await expect(panel.getByText("Direct board role", { exact: true })).toHaveCount(0);
    expect(reads.at(-1)?.searchParams.get("card_uid")).toBe("00000000007");
    state.failure = 404;
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(panel.getByRole("alert")).toBeVisible();
    await expect(panel.getByText("Card read allowed", { exact: true })).toHaveCount(0);
    state.failure = 0;
    await panel.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(panel.getByText("Card read allowed", { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});

test("card search failure and empty results hide stale options", async ({ page, baseURL }) => {
    const { state, errors } = await setup(page, baseURL),
        panel = await open(page);
    await choose(page);
    state.cardFailure = 500;
    await page.getByRole("textbox", { name: "Search readable cards", exact: true }).fill("Failed");
    await expect(panel.getByRole("alert")).toContainText("Unable to search readable cards");
    await expect(panel.getByRole("combobox", { name: "Select cards", exact: true })).toHaveCount(0);
    state.cardFailure = 0;
    state.cardEmpty = true;
    await page.getByRole("textbox", { name: "Search readable cards", exact: true }).fill("Empty");
    await expect(panel.getByText("No readable cards found", { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
});

for (const transition of ["account", "session"] as const)
    test(`card search rejects the old response after ${transition} switch`, async ({ page, baseURL }) => {
        const { state, searchReads, errors } = await setup(page, baseURL);
        await open(page);
        await choose(page);
        state.searchDelay = true;
        await page.getByRole("textbox", { name: "Search readable cards", exact: true }).fill("Readable");
        await expect.poll(() => searchReads.length).toBe(1);
        state.cardTitle = "Current session card";
        if (transition === "account") state.authUID = "other-admin";
        await page.evaluate(async () => {
            const authPath = "/src/core/stores/AuthStore.ts",
                apiPath = "/src/core/helpers/Api.ts";
            const [{ default: auth }, { api }] = await Promise.all([import(authPath), import(apiPath)]);
            await auth.getState().updateToken("fixture-next-session", api);
        });
        // Native identity refresh closes user details; reopen with the same board/query.
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await open(page);
        await choose(page);
        await page.getByRole("textbox", { name: "Search readable cards", exact: true }).fill("Readable");
        await expect.poll(() => searchReads.length).toBe(2);
        state.searchRelease();
        await page.getByRole("combobox", { name: "Select cards", exact: true }).click();
        await expect(page.getByRole("option", { name: "Current session card", exact: true })).toBeVisible();
        await expect(page.getByRole("option", { name: "Readable shared card", exact: true })).toHaveCount(0);
        expect(errors).toEqual([]);
    });
