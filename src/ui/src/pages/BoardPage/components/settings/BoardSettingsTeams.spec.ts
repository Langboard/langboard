import { expect, test, Page } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/settings/BoardSettingsTeams.fixture.html";
const member = { uid: "Member00001", firstname: "Ada", lastname: "Member", username: "ada" };
const candidate = { uid: "Member00002", firstname: "Grace", lastname: "Candidate", username: "grace" };
const teamUID = "Team0000001";
async function transport(
    page: Page,
    options: {
        manage?: boolean;
        importGroup?: boolean;
        ambiguous?: boolean;
        conflict?: number;
        impact?: "ongoing" | "none" | "unavailable";
        reviewError?: number;
    } = {}
) {
    let team = { uid: teamUID, name: "Engineering", revision: 1, member_uids: [member.uid], members: [member], member_count: 1 };
    const requests: { method: string; path: string; data: Record<string, unknown>; query: string }[] = [];
    let failed = false;
    let detailReads = 0;
    await page.route(/\/board\/Board[0-9]+\/teams(?:[/?]|$)/, async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const data = request.postDataJSON() ?? {};
        requests.push({ method, path: url.pathname, data, query: url.search });
        if (method === "GET") {
            if (url.pathname.endsWith("/candidates")) return route.fulfill({ json: { records: [candidate], total_records: 1 } });
            if (url.pathname.endsWith("/group-preview"))
                return route.fulfill({
                    json: {
                        team_revision: team.revision,
                        group: { uid: "Group000001", display_name: "Canonical Engineering" },
                        snapshot: "a".repeat(64),
                        eligible_count: 2,
                        existing_count: 1,
                        excluded_count: 3,
                        target_count: 1,
                        records: [candidate],
                    },
                });
            if (url.pathname.endsWith(`/${teamUID}`)) {
                detailReads++;
                if (options.reviewError && detailReads > 1) return route.fulfill({ status: options.reviewError, json: {} });
                return route.fulfill({
                    json: {
                        team,
                        decision_impact:
                            options.impact === "unavailable"
                                ? { status: "unavailable" }
                                : { status: "available", ongoing: options.impact === "ongoing", scope: "readable_cards" },
                    },
                });
            }
            return route.fulfill({
                json: {
                    records: [team],
                    total_records: 16,
                    abilities: { manage: options.manage ?? true, import_group: options.importGroup ?? true },
                },
            });
        }
        if (options.conflict) return route.fulfill({ status: options.conflict, json: {} });
        if (options.ambiguous && !failed) {
            failed = true;
            return route.abort("failed");
        }
        if (method === "PATCH") team = { ...team, name: String(data.name), revision: team.revision + 1 };
        if (method === "POST" && url.pathname.endsWith("/members"))
            team = { ...team, members: [member, candidate], member_uids: [member.uid, candidate.uid], member_count: 2, revision: team.revision + 1 };
        if (method === "DELETE" && url.pathname.endsWith("/members"))
            team = { ...team, members: [candidate], member_uids: [candidate.uid], member_count: 1, revision: team.revision + 1 };
        return route.fulfill({
            json: {
                team_uid: teamUID,
                revision: team.revision,
                replayed: !!options.ambiguous,
                deleted: method === "DELETE" && !url.pathname.endsWith("/members"),
            },
        });
    });
    await page.route("**/settings/groups?**", (route) =>
        route.fulfill({ json: { records: [{ uid: "Group000001", display_name: "Canonical Engineering" }] } })
    );
    return requests;
}
async function openTeam(page: Page) {
    await page.goto(fixture);
    await page.getByRole("button", { name: "Toggle teams" }).click();
    await page.getByRole("button", { name: "Engineering (1)", exact: true }).click();
    await expect(page.getByText("Revision 1", { exact: true })).toBeVisible();
}

test("inactive is silent; keyboard CRUD, partial candidates, snapshot and 320px layout", async ({ page }) => {
    const requests = await transport(page);
    await page.goto(fixture);
    await page.waitForTimeout(450);
    expect(requests).toHaveLength(0);
    await page.getByRole("button", { name: "Toggle teams" }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect.poll(() => requests.some((r) => r.path.endsWith("/teams") && r.query.includes("page=2"))).toBe(true);
    await page.getByLabel("Search teams", { exact: true }).fill("engine");
    await expect
        .poll(() => requests.some((r) => r.path.endsWith("/teams") && r.query.includes("query=engine") && r.query.includes("page=1")))
        .toBe(true);
    await page.getByRole("button", { name: "Engineering (1)", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Revision 1", { exact: true })).toBeVisible();
    await page.getByLabel("Team name", { exact: true }).fill("Renamed");
    await page.getByRole("button", { name: "Rename", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Renamed", exact: true })).toBeVisible();
    await page.getByLabel("Search board participants by partial name, email or username").fill("gra");
    await expect.poll(() => requests.some((r) => r.path.endsWith("/candidates") && r.query.includes("query=gra"))).toBe(true);
    await page.getByRole("button", { name: "Add Grace Candidate", exact: true }).click();
    await expect(page.getByText("Revision 3", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Remove Ada Member", exact: true }).click();
    await expect(page.getByText("Revision 4", { exact: true })).toBeVisible();
    await page.getByLabel("Canonical group", { exact: true }).selectOption("Group000001");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Excluded from import: 3", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Import 1 participants", exact: true })).toBeDisabled();
    await page.getByLabel("I confirm adding 1 participants").focus();
    await page.keyboard.press("Space");
    await page.getByRole("button", { name: "Import 1 participants", exact: true }).click();
    await expect
        .poll(() =>
            requests.some(
                (r) =>
                    r.path.endsWith("/group-add") &&
                    r.data.confirmed_count === 1 &&
                    r.data.expected_revision === 4 &&
                    r.data.snapshot === "a".repeat(64)
            )
        )
        .toBe(true);
    await page.getByRole("button", { name: "Review deletion", exact: true }).click();
    await expect(page.getByText("No ongoing Decisions referencing this team", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete team", exact: true })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByLabel("I have reviewed the current Decision impact and confirm deletion").check();
    await page.getByRole("button", { name: "Delete team", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Renamed", exact: true })).toHaveCount(0);
    await page.getByLabel("New team name", { exact: true }).fill("New team");
    await page.getByRole("button", { name: "Create team", exact: true }).click();
    await expect.poll(() => requests.some((r) => r.method === "POST" && r.data.name === "New team")).toBe(true);
    expect(requests.every((r) => r.path.includes("/teams"))).toBe(true);
});

test("ambiguous retry binds exact UUID and payload then reloads historical receipt", async ({ page }) => {
    const requests = await transport(page, { ambiguous: true });
    await openTeam(page);
    await page.getByLabel("Team name", { exact: true }).fill("Retry name");
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
    await expect(page.getByLabel("Team name", { exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Retry name", exact: true })).toBeVisible();
    const writes = requests.filter((r) => r.method === "PATCH");
    expect(writes).toHaveLength(2);
    expect(writes[0].data).toEqual(writes[1].data);
    expect(writes[0].data.request_id).toMatch(/^[a-f0-9-]{36}$/);
    expect(requests.filter((r) => r.method === "GET" && r.path.endsWith(`/${teamUID}`)).length).toBeGreaterThan(1);
});
for (const status of [401, 403, 404, 409])
    test(`${status} invalidates authority and snapshot; explicit reload retains safe draft`, async ({ page }) => {
        const requests = await transport(page, { conflict: status });
        await openTeam(page);
        await page.getByLabel("New team name", { exact: true }).fill("Safe draft");
        await page.getByLabel("Team name", { exact: true }).fill("Unsaved rename");
        await page.getByLabel("Canonical group", { exact: true }).selectOption("Group000001");
        await page.getByRole("button", { name: "Preview", exact: true }).click();
        await page.getByLabel("I confirm adding 1 participants").check();
        await page.getByRole("button", { name: "Import 1 participants", exact: true }).click();
        await expect(page.getByRole("status")).toContainText("Reload before continuing");
        await expect(page.getByRole("button", { name: "Create team", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
        const count = requests.length;
        await page.waitForTimeout(350);
        expect(requests).toHaveLength(count);
        await page.getByRole("button", { name: "Reload", exact: true }).click();
        await expect(page.getByLabel("New team name", { exact: true })).toHaveValue("Safe draft");
        await expect(page.getByLabel("Team name", { exact: true })).toHaveValue("Unsaved rename");
        await expect(page.getByRole("button", { name: "Import 1 participants", exact: true })).toHaveCount(0);
    });

test("server abilities hide writes and canonical group requests", async ({ page }) => {
    await transport(page, { manage: false, importGroup: false });
    const groups: string[] = [];
    page.on("request", (request) => {
        if (request.url().includes("/settings/groups")) groups.push(request.url());
    });
    await openTeam(page);
    await expect(page.getByRole("button", { name: "Create team", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove Ada Member", exact: true })).toHaveCount(0);
    await page.waitForTimeout(350);
    expect(groups).toHaveLength(0);
});

test("inactive component stops requests across board/account changes", async ({ page }) => {
    const requests = await transport(page);
    await openTeam(page);
    await page.getByRole("button", { name: "Toggle teams" }).click();
    const count = requests.length;
    await page.waitForTimeout(350);
    expect(requests).toHaveLength(count);
    await page.getByRole("button", { name: "Switch board" }).click();
    await page.getByRole("button", { name: "Switch account" }).click();
    await page.getByRole("button", { name: "Toggle teams" }).click();
    await expect(page.getByRole("heading", { name: "Engineering", exact: true })).toHaveCount(0);
    await expect.poll(() => requests.some((r) => r.path === "/board/Board000002/teams")).toBe(true);
});

for (const change of ["Switch board", "Switch account", "Toggle teams"])
    test(`delayed detail cannot overwrite after ${change}`, async ({ page }) => {
        await transport(page);
        let release!: () => void;
        const held = new Promise<void>((resolve) => {
            release = resolve;
        });
        let entered = false;
        await page.route("**/board/Board000001/teams/Team0000001", async (route) => {
            entered = true;
            await held;
            await route
                .fulfill({ json: { team: { uid: teamUID, name: "Stale detail", revision: 99, member_uids: [], members: [], member_count: 0 } } })
                .catch(() => {});
        });
        await page.goto(fixture);
        await page.getByRole("button", { name: "Toggle teams" }).click();
        await page.getByRole("button", { name: "Engineering (1)", exact: true }).click();
        await expect.poll(() => entered).toBe(true);
        await page.getByRole("button", { name: change, exact: true }).click();
        release();
        await page.waitForTimeout(350);
        await expect(page.getByRole("heading", { name: "Stale detail", exact: true })).toHaveCount(0);
    });

test("manage without import ability never searches canonical groups", async ({ page }) => {
    await transport(page, { manage: true, importGroup: false });
    const groups: string[] = [];
    page.on("request", (request) => {
        if (request.url().includes("/settings/groups")) groups.push(request.url());
    });
    await openTeam(page);
    await expect(page.getByRole("button", { name: "Rename", exact: true })).toBeVisible();
    await expect(page.getByLabel("Canonical group", { exact: true })).toHaveCount(0);
    await page.waitForTimeout(350);
    expect(groups).toHaveLength(0);
});

for (const impact of ["ongoing", "none", "unavailable"] as const)
    test(`fresh deletion review: ${impact}`, async ({ page }) => {
        const requests = await transport(page, { impact });
        await openTeam(page);
        const confirm = page.getByLabel("I have reviewed the current Decision impact and confirm deletion");
        await expect(confirm).toBeDisabled();
        await page.getByRole("button", { name: "Review deletion", exact: true }).click();
        await expect.poll(() => requests.filter((r) => r.method === "GET" && r.path.endsWith(`/${teamUID}`)).length).toBe(2);
        const alert = page.getByRole("alert");
        if (impact === "unavailable") {
            await expect(alert).toContainText("Decision impact could not be checked");
            await expect(confirm).toBeDisabled();
            await expect(page.getByRole("button", { name: "Delete team", exact: true })).toBeDisabled();
        } else {
            await expect(alert).toContainText(
                impact === "ongoing" ? "Ongoing Decisions on cards you can read reference this team" : "No ongoing Decisions referencing this team"
            );
            await confirm.check();
            await page.getByRole("button", { name: "Delete team", exact: true }).click();
            await expect.poll(() => requests.some((r) => r.method === "DELETE" && r.data.expected_revision === 1)).toBe(true);
        }
    });

for (const status of [403, 409, 500])
    test(`failed deletion review ${status} cannot authorize deletion`, async ({ page }) => {
        const requests = await transport(page, { reviewError: status });
        await openTeam(page);
        await page.getByRole("button", { name: "Review deletion", exact: true }).click();
        await expect(page.getByRole("status")).toContainText(status === 500 ? "Request failed" : "Reload before continuing");
        expect(requests.some((r) => r.method === "DELETE")).toBe(false);
        const confirm = page.getByLabel("I have reviewed the current Decision impact and confirm deletion");
        if (status === 500) await expect(confirm).toBeDisabled();
        else await expect(confirm).toHaveCount(0);
    });
