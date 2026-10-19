import { expect, test } from "@playwright/test";
for (const width of [1280, 700, 390]) {
    test(`recent views keep projects accessible at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html");
        const recent = page.getByRole("region", { name: "Recent cards", exact: true });
        await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
        await expect(recent.getByRole("button", { name: "Card 0", exact: true })).toHaveCount(0);
        const projects = page.locator("[data-explorer-project-list]");
        const measure = () => projects.evaluate((e) => ({ height: e.clientHeight, bottom: e.getBoundingClientRect().bottom }));
        expect((await measure()).height).toBeGreaterThanOrEqual(96);
        expect((await measure()).bottom).toBeLessThanOrEqual(844);
        await recent.getByRole("button", { name: "Show older cards (4)", exact: true }).press("Enter");
        await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(14);
        expect((await measure()).height).toBeGreaterThanOrEqual(96);
        await recent.getByRole("button", { name: "Card 0", exact: true }).click();
        await expect(page.getByTestId("route")).toHaveText("/board/fixture/0");
        await page.getByRole("button", { name: "Read oldest again", exact: true }).click();
        await recent.getByRole("button", { name: "Show fewer cards", exact: true }).click();
        await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
        await expect(recent.getByRole("button", { name: /^Card \d+$/ }).first()).toHaveText("Card 0");
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        expect(await page.locator("button button").count()).toBe(0);
        await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?count=10");
        await expect(recent.getByRole("button", { name: /Show older|Show fewer/ })).toHaveCount(0);
        await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
    });
}

test("offline deletion and permission loss remove only confirmed unavailable history", async ({ page }) => {
    let status = 200;
    let calls = 0;
    await page.route("**/dashboard/cards/available", async (route) => {
        if (route.request().method() === "OPTIONS") {
            await route.fulfill({
                status: 204,
                headers: {
                    "Access-Control-Allow-Origin": "http://127.0.0.1:4188",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "content-type, content-encoding, authorization",
                },
            });
            return;
        }
        calls++;
        const cards = route.request().postDataJSON().cards as { project_uid: string; card_uid: string }[];
        await route.fulfill({
            status,
            headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:4188", "Access-Control-Allow-Credentials": "true" },
            contentType: "application/json",
            body: JSON.stringify({ cards: cards.filter((card) => card.card_uid !== "13" && status !== 201) }),
        });
    });
    await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html");
    const recent = page.getByRole("region", { name: "Recent cards", exact: true });
    await expect.poll(() => calls).toBeGreaterThan(0);
    await expect(recent.getByRole("button", { name: "Card 13", exact: true })).toHaveCount(0);
    await expect(recent.getByRole("button", { name: "Card 12", exact: true })).toBeVisible();
    await expect(recent.getByRole("button", { name: "Show older cards (3)", exact: true })).toBeVisible();
    status = 503;
    const previous = calls;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => calls).toBeGreaterThan(previous);
    await expect(recent.getByRole("button", { name: "Card 12", exact: true })).toBeVisible();
    status = 201;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(0);
});

test("online recovery prunes inaccessible cards while preserving pins and close actions", async ({ page }) => {
    let available = false;
    let calls = 0;
    await page.route("**/dashboard/cards/available", async (route) => {
        const headers = {
            "Access-Control-Allow-Origin": "http://127.0.0.1:4188",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "content-type, content-encoding, authorization",
        };
        if (route.request().method() === "OPTIONS") {
            await route.fulfill({ status: 204, headers });
            return;
        }
        calls++;
        const cards = route.request().postDataJSON().cards as { project_uid: string; card_uid: string }[];
        await route.fulfill({
            status: available ? 200 : 503,
            headers,
            contentType: "application/json",
            body: JSON.stringify({ cards: cards.filter((card) => card.card_uid !== "13") }),
        });
    });
    await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html");
    const recent = page.getByRole("region", { name: "Recent cards", exact: true });
    await expect.poll(() => calls).toBeGreaterThan(0);
    await expect(recent.getByRole("button", { name: "Card 13", exact: true })).toBeVisible();
    available = true;
    const previous = calls;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect.poll(() => calls).toBeGreaterThan(previous);
    await expect(recent.getByRole("button", { name: "Card 13", exact: true })).toHaveCount(0);
    await expect(recent.getByRole("button", { name: "Card 12", exact: true })).toBeVisible();
    await recent.getByRole("button", { name: "Show older cards (3)", exact: true }).click();
    const oldest = recent.locator(".group").filter({ has: page.getByRole("button", { name: "Card 0", exact: true }) });
    await expect(oldest.getByRole("button", { name: "Unpin card", exact: true })).toBeVisible();
    await oldest.getByRole("button", { name: "Unpin card", exact: true }).click();
    await expect(oldest.getByRole("button", { name: "Pin card", exact: true })).toBeVisible();
    await oldest.getByRole("button", { name: "Pin card", exact: true }).click();
    await oldest.getByRole("button", { name: "Close card from list", exact: true }).click();
    await expect(recent.getByRole("button", { name: "Card 0", exact: true })).toHaveCount(0);
    await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(12);
});

test("hidden or collapsed explorer performs no history scan and expansion validates older cards on demand", async ({ page }) => {
    const checked = new Set<string>();
    let calls = 0;
    await page.route("**/dashboard/cards/available", async (route) => {
        const headers = {
            "Access-Control-Allow-Origin": "http://127.0.0.1:4188",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "content-type, content-encoding, authorization",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        calls++;
        const cards = route.request().postDataJSON().cards as { project_uid: string; card_uid: string }[];
        cards.forEach((card) => checked.add(card.card_uid));
        await route.fulfill({ status: 200, headers, json: { cards } });
    });
    await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?multi-board&hidden");
    const recent = page.getByRole("region", { name: "Recent cards", exact: true });
    await expect(recent.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    expect(calls).toBe(0);
    await page.getByRole("button", { name: "Toggle explorer visibility", exact: true }).click();
    await expect.poll(() => checked.size).toBe(10);
    expect(checked.has("0")).toBe(false);
    await recent.getByRole("button", { name: "Show older cards (4)", exact: true }).click();
    await expect.poll(() => checked.size).toBe(14);
    await recent.getByRole("button", { name: /^Recent cards/ }).click();
    const beforeFocus = calls;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    expect(calls).toBe(beforeFocus);
});

const cors = {
    "Access-Control-Allow-Origin": "http://127.0.0.1:4188",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, content-encoding, authorization",
};
for (const older of [404, 405, 0]) {
    test(`ten-board component uses ${older ? `ten older-host ${older} probes` : "one supported batch"}`, async ({ page }) => {
        let batches = 0;
        const boards = new Set<string>();
        await page.route("**/dashboard/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
            batches++;
            const cards = route.request().postDataJSON().cards;
            expect(cards).toHaveLength(10);
            await route.fulfill({ status: older || 200, headers: cors, json: { cards } });
        });
        await page.route("**/board/*/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
            boards.add(new URL(route.request().url()).pathname);
            await route.fulfill({ status: 200, headers: cors, json: route.request().postDataJSON() });
        });
        await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?multi-board&count=10");
        await expect.poll(() => batches).toBe(1);
        await expect(page.getByRole("button", { name: "Card 9", exact: true })).toBeVisible();
        if (older) await expect.poll(() => boards.size).toBe(10);
        else expect(boards.size).toBe(0);
    });
}
for (const failure of [503, 401, "network"] as const) {
    test(`${failure} retains history without fanout`, async ({ page }) => {
        let calls = 0;
        let legacy = 0;
        await page.route("**/dashboard/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
            calls++;
            if (failure === "network") return route.abort();
            await route.fulfill({ status: failure, headers: cors, json: { cards: [] } });
        });
        await page.route("**/board/*/cards/available", (route) => {
            legacy++;
            return route.abort();
        });
        await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?multi-board&count=10");
        await expect.poll(() => calls).toBe(1);
        await expect(page.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
        expect(legacy).toBe(0);
    });
}
for (const width of [1280, 390]) {
    test(`actual tray uses batch and retains failures at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const requested: string[][] = [];
        let status = 503;
        await page.route("**/dashboard/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
            const cards = route.request().postDataJSON().cards as { card_uid: string }[];
            requested.push(cards.map((card) => card.card_uid));
            await route.fulfill({ status, headers: cors, json: { cards: [] } });
        });
        await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?tray&hidden");
        await expect.poll(() => requested.length).toBe(1);
        expect(requested[0]).toEqual(["tray"]);
        await expect(page.locator("[data-card-flip-tray]")).toBeVisible();
        status = 200;
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await expect(page.locator("[data-card-flip-tray]")).toHaveCount(0);
    });
}

test("late aborted response after account switch retains original account records", async ({ page }) => {
    let release: (() => void) | undefined;
    let calls = 0;
    await page.route("**/dashboard/cards/available", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
        calls++;
        if (calls > 1) return route.fulfill({ status: 503, headers: cors, json: {} });
        await new Promise<void>((resolve) => {
            release = resolve;
        });
        await route.fulfill({ status: 200, headers: cors, json: { cards: [] } }).catch(() => {});
    });
    await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?count=10");
    await expect.poll(() => calls).toBe(1);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    release!();
    await expect(page.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(0);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect.poll(() => calls).toBe(2);
    await expect(page.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(10);
});

test("newly opened records survive an older pending snapshot", async ({ page }) => {
    let release: (() => void) | undefined;
    let calls = 0;
    await page.route("**/dashboard/cards/available", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
        calls++;
        if (calls > 1) return route.fulfill({ status: 503, headers: cors, json: {} });
        await new Promise<void>((resolve) => {
            release = resolve;
        });
        await route.fulfill({ status: 200, headers: cors, json: { cards: [] } }).catch(() => {});
    });
    await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?count=10");
    await expect.poll(() => calls).toBe(1);
    await page.getByRole("button", { name: "Open new card", exact: true }).click();
    release!();
    await expect.poll(() => calls).toBe(2);
    await expect(page.getByRole("button", { name: "New card", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(9);
});

for (const confirmedStatus of [403, 200]) {
    test(`mixed legacy ${confirmedStatus} and service failure prunes only confirmed keys and stops probing`, async ({ page }) => {
        const probes: string[] = [];
        let batches = 0;
        await page.route("**/dashboard/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
            batches++;
            // Reconciliation rerenders the component; subsequent failures must retain every unchecked key.
            await route.fulfill({ status: batches === 1 ? 404 : 503, headers: cors, json: {} });
        });
        await page.route("**/board/*/cards/available", async (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
            const project = new URL(route.request().url()).pathname.split("/")[2];
            probes.push(project);
            const status = probes.length === 1 ? confirmedStatus : 503;
            await route.fulfill({ status, headers: cors, json: { card_uids: [] } });
        });
        await page.goto("/src/pages/DashboardPage/components/recent-cards.fixture.html?multi-board&count=10");
        await expect.poll(() => probes.length).toBe(2);
        await expect(page.getByRole("button", { name: "Card 0", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: /^Card \d+$/ })).toHaveCount(9);
        await expect(page.getByRole("button", { name: "Card 1", exact: true })).toBeVisible();
        expect(probes).toEqual(["fixture-0", "fixture-1"]);
        await expect.poll(() => batches).toBe(2);
    });
}
