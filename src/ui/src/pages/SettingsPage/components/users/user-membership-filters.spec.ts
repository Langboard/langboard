import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

// Synthetic HTTP isolates actual Users components from production identities.
async function setup(page: Page, mode = "both", labels?: { group: string; board: string }) {
    page.on("pageerror", (error) => console.error("Browser error:", error.stack));
    const reads: URL[] = [];
    const choices: URL[] = [];
    let delay = false;
    let release: (() => void) | undefined;
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
        if (url.pathname.endsWith("/settings/employee-membership"))
            return route.fulfill({ json: { issuer: "", configured: false, group_uids: [], groups: [] }, headers });
        if (url.pathname.endsWith("/settings/roles"))
            return route.fulfill({
                json: {
                    setting_role_actions: mode === "both" ? ["user_read", "group_read"] : ["user_read"],
                    api_key_role_actions: [],
                    mcp_role_actions: [],
                },
                headers,
            });
        if (url.pathname.endsWith("/settings/groups") || url.pathname.endsWith("/filter-projects")) {
            choices.push(url);
            const group = url.pathname.endsWith("/settings/groups");
            return route.fulfill({
                json: {
                    records: [
                        {
                            uid: group ? "00000000010" : "00000000020",
                            display_name: labels
                                ? group
                                    ? labels.group
                                    : labels.board
                                : group
                                  ? url.searchParams.get("page") === "2"
                                      ? "Raw <group> % later"
                                      : "First group"
                                  : "Readable board",
                            revision: 1,
                        },
                    ],
                    total_records: url.searchParams.get("query") ? 1 : 16,
                },
                headers,
            });
        }
        if (url.pathname.endsWith("/settings/users/page")) {
            reads.push(url);
            if (delay) {
                delay = false;
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            const filtered = url.searchParams.has("group_uid") || url.searchParams.has("project_uid");
            return route.fulfill({
                json: {
                    records: [
                        {
                            uid: "00000000001",
                            type: "user",
                            firstname: "Member",
                            lastname: "Person",
                            username: "member",
                            email: "member@example.invalid",
                            activated_at: "2026-01-01T00:00:00Z",
                            created_at: "2026-01-01T00:00:00Z",
                            updated_at: "2026-01-01T00:00:00Z",
                            is_admin: false,
                            setting_role_actions: [],
                            api_key_role_actions: [],
                            mcp_role_actions: [],
                        },
                    ],
                    total_records: filtered ? 1 : 20,
                    account_counts: { total: filtered ? 1 : 20, active: filtered ? 1 : 20, inactive: 0, no_recent_sign_in: 0 },
                    full_access_emails: [],
                },
                headers,
            });
        }
        return route.fulfill({ json: {}, headers });
    });
    await page.goto(`/src/pages/SettingsPage/components/users/group-directory.fixture.html?mode=${mode}`);
    await expect(page.getByText("member@example.invalid", { exact: true })).toBeVisible();
    return {
        reads,
        choices,
        delay: () => {
            delay = true;
        },
        release: () => release?.(),
    };
}

test("later-page raw group selection intersects board search and resets user page", async ({ page }) => {
    const state = await setup(page);
    expect(state.choices).toHaveLength(0);
    await page.getByRole("button", { name: "Next Page", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("page")).toBe("2");
    const groups = page.getByRole("region", { name: "Filter by group", exact: true });
    await groups.getByRole("button", { name: "Filter by group", exact: true }).click();
    await groups.getByRole("button", { name: "Next Page", exact: true }).click();
    await groups.getByRole("button", { name: "Raw <group> % later", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("group_uid")).toBe("00000000010");
    expect(state.reads.at(-1)?.searchParams.get("page")).toBe("1");
    const boards = page.getByRole("region", { name: "Filter by participating board", exact: true });
    await boards.getByRole("button", { name: "Filter by participating board", exact: true }).click();
    await boards.getByRole("textbox", { name: "Search boards", exact: true }).fill("Readable");
    await expect.poll(() => state.choices.at(-1)?.searchParams.get("query")).toBe("Readable");
    await boards.getByRole("button", { name: "Readable board", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("project_uid")).toBe("00000000020");
    expect(state.reads.at(-1)?.searchParams.get("group_uid")).toBe("00000000010");
    await page.screenshot({
        path: "/Volumes/yamon/Projects/langboard/artifacts/iam-membership-filters-20261123/chrome-selected.png",
        fullPage: true,
    });
    await groups.getByRole("button", { name: "Clear membership filter", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("group_uid")).toBe(false);
    expect(state.reads.at(-1)?.searchParams.get("project_uid")).toBe("00000000020");
});

test("UserRead alone cannot expose or prefetch group choices", async ({ page }) => {
    const state = await setup(page, "user");
    await expect(page.getByRole("region", { name: "Filter by group", exact: true })).toHaveCount(0);
    expect(state.choices).toHaveLength(0);
});

test("revocation hides an outstanding filtered response", async ({ page }) => {
    const state = await setup(page);
    const groups = page.getByRole("region", { name: "Filter by group", exact: true });
    await groups.getByRole("button", { name: "Filter by group", exact: true }).click();
    state.delay();
    await groups.getByRole("button", { name: "First group", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("group_uid")).toBe("00000000010");
    await expect(page.getByText("member@example.invalid", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Revoke group permission", exact: true }).click();
    state.release();
    await expect(page.getByText("member@example.invalid", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Filter by group", exact: true })).toHaveCount(0);
});

for (const action of ["End session", "Switch account"]) {
    test(`${action} discards outstanding directory selection`, async ({ page }) => {
        const state = await setup(page);
        const groups = page.getByRole("region", { name: "Filter by group", exact: true });
        await groups.getByRole("button", { name: "Filter by group", exact: true }).click();
        state.delay();
        await groups.getByRole("button", { name: "First group", exact: true }).click();
        await expect.poll(() => state.reads.at(-1)?.searchParams.get("group_uid")).toBe("00000000010");
        await page.getByRole("button", { name: action, exact: true }).click();
        state.release();
        await expect(page.getByText("member@example.invalid", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("region", { name: "Filter by group", exact: true })).toHaveCount(0);
    });
}

test("clearing a pending group filter discards the stale response", async ({ page }) => {
    const state = await setup(page);
    const groups = page.getByRole("region", { name: "Filter by group", exact: true });
    await groups.getByRole("button", { name: "Filter by group", exact: true }).click();
    state.delay();
    await groups.getByRole("button", { name: "First group", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.get("group_uid")).toBe("00000000010");
    await groups.getByRole("button", { name: "Clear membership filter", exact: true }).click();
    await expect.poll(() => state.reads.at(-1)?.searchParams.has("group_uid")).toBe(false);
    state.release();
    await expect(page.getByRole("region", { name: "Account summary" }).getByText("20", { exact: true }).first()).toBeVisible();
});

for (const width of [390, 1440]) {
    test(`membership labels and email remain readable with local scrolling at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const labels = {
            group: `Raw <group> % & " ${"unbroken-group-".repeat(12)} end`,
            board: `Raw <board> % & " ${"unbroken-board-".repeat(12)} end`,
        };
        const state = await setup(page, "both", labels);
        for (const [kind, name, raw] of [
            ["group", "Filter by group", labels.group],
            ["project", "Filter by participating board", labels.board],
        ] as const) {
            const region = page.getByRole("region", { name, exact: true });
            const trigger = region.getByRole("button", { name, exact: true });
            await trigger.focus();
            await page.keyboard.press("Enter");
            const choice = region.getByRole("button", { name: raw, exact: true });
            await expect(choice).toBeVisible();
            await choice.focus();
            await page.keyboard.press("Enter");
            await expect.poll(() => state.reads.at(-1)?.searchParams.has(`${kind}_uid`)).toBe(true);
            const selected = region.getByRole("button", { name: `${name}: ${raw}`, exact: true });
            await expect(selected).toBeVisible();
            await expect(selected).toHaveAccessibleName(`${name}: ${raw}`);
            const bounds = await selected.boundingBox();
            expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
            // The entire raw value remains text; JSX never creates markup from names.
            expect(await selected.textContent()).toBe(`${name}: ${raw}`);
            await expect(selected.locator("group, board")).toHaveCount(0);
        }
        const email = page.getByText("member@example.invalid", { exact: true });
        const metrics = await email.evaluate((node) => {
            const cell = node.closest("td")!;
            const row = cell.closest("tr")!;
            const table = cell.closest("table")!;
            const scroller = table.parentElement!;
            return {
                viewport: innerWidth,
                documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                cellWidth: cell.getBoundingClientRect().width,
                emailHeight: node.getBoundingClientRect().height,
                rowHeight: row.getBoundingClientRect().height,
                tableWidth: table.getBoundingClientRect().width,
                scrollerWidth: scroller.clientWidth,
                scrollWidth: scroller.scrollWidth,
                overflowX: getComputedStyle(scroller).overflowX,
            };
        });
        expect(metrics.documentOverflow).toBe(0);
        expect(metrics.cellWidth).toBeGreaterThanOrEqual(300);
        expect(metrics.emailHeight).toBeLessThanOrEqual(44);
        expect(metrics.rowHeight).toBeLessThan(140);
        expect(metrics.overflowX).toBe("auto");
        expect(metrics.scrollWidth).toBeGreaterThan(metrics.scrollerWidth);
        const moved = await email.evaluate((node) => {
            const scroller = node.closest("table")!.parentElement!;
            scroller.scrollLeft = scroller.scrollWidth;
            return scroller.scrollLeft;
        });
        expect(moved).toBeGreaterThan(0);
        await email.evaluate((node) => {
            node.closest("table")!.parentElement!.scrollLeft = 0;
        });
        writeFileSync(
            `/Volumes/yamon/Projects/langboard/artifacts/iam-membership-filters-20261123/readability-${width}.json`,
            JSON.stringify(metrics, null, 2)
        );
        await page.screenshot({
            path: `/Volumes/yamon/Projects/langboard/artifacts/iam-membership-filters-20261123/chrome-readable-${width}.png`,
            fullPage: true,
        });
    });
}
