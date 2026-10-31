import { expect, test } from "@playwright/test";
for (const width of [1280, 390]) {
    test(`next action save, failure, retry and clear at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        const writes: unknown[] = [];
        let fail = false;
        page.on("pageerror", (error) => {
            throw error;
        });
        await page.route("**/board/fixture-board/card/fixture-card/details", async (route) => {
            const headers = {
                "Access-Control-Allow-Origin": new URL(page.url()).origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "PUT,OPTIONS",
                "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
            };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            const body = route.request().postDataJSON();
            writes.push(body);
            await route.fulfill({
                status: fail ? 500 : 200,
                headers,
                json: fail ? { detail: "Save failed" } : { next_action: body.next_action.trim() || null },
            });
        });
        await page.goto("/src/pages/BoardPage/components/card/next-action.fixture.html");
        await page.getByRole("button", { name: "Edit next action", exact: true }).click();
        await page.getByRole("textbox", { name: "Next action", exact: true }).fill("Request 자료");
        await page.getByRole("button", { name: "Save next action", exact: true }).click();
        await expect(page.locator("output")).toHaveText("Request 자료");
        expect(writes[0]).toEqual({ next_action: "Request 자료" });
        await page.getByRole("button", { name: "Edit next action", exact: true }).click();
        fail = true;
        await page.getByRole("textbox").fill("Retry preserved");
        await page.getByRole("button", { name: "Save next action", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.locator("output")).toHaveText("Request 자료");
        await expect(page.getByRole("textbox")).toHaveValue("Retry preserved");
        fail = false;
        await page.getByRole("button", { name: "Save next action", exact: true }).click();
        await expect(page.locator("output")).toHaveText("Retry preserved");
        await page.getByRole("button", { name: "한국어", exact: true }).click();
        await page.getByRole("button", { name: "다음 행동 편집", exact: true }).click();
        await page.getByRole("button", { name: "다음 행동 지우기", exact: true }).click();
        await expect(page.locator("output")).toHaveText("empty");
        await expect(page.getByText("다음 행동 없음", { exact: true })).toBeVisible();
        expect(writes.at(-1)).toEqual({ next_action: "" });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}
test("reader sees text without edit controls", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/card/next-action.fixture.html?readonly");
    await expect(page.getByText("Confirm deployment", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit next action" })).toHaveCount(0);
    await expect(page.getByRole("textbox")).toHaveCount(0);
});

test("pending save retains ownership across permission loss and regain", async ({ page }) => {
    let release: () => void = () => {};
    const barrier = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/board/fixture-board/card/fixture-card/details", async (route) => {
        const headers = {
            "Access-Control-Allow-Origin": new URL(page.url()).origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "PUT,OPTIONS",
            "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
        };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        await barrier;
        await route.fulfill({ status: 200, headers, json: { next_action: "Saved original request" } });
    });
    await page.goto("/src/pages/BoardPage/components/card/next-action.fixture.html");
    await page.getByRole("button", { name: "Edit next action", exact: true }).click();
    await page.getByRole("textbox").fill("Saved original request");
    await page.getByRole("button", { name: "Save next action", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save next action", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Toggle permission", exact: true }).click();
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await page.getByRole("button", { name: "Toggle permission", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit next action", exact: true })).toBeDisabled();
    release();
    await expect(page.locator("output")).toHaveText("Saved original request");
    await page.getByRole("button", { name: "Edit next action", exact: true }).click();
    await page.getByRole("textbox").fill("New draft");
    await expect(page.getByRole("textbox")).toHaveValue("New draft");
});

test("board display omits legacy empty action and bounds long authored text", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 850 });
    await page.goto("/src/pages/BoardPage/components/card/next-action.fixture.html?board&empty");
    await expect(page.locator("[data-board-next-action]")).toBeEmpty();
    await page.goto("/src/pages/BoardPage/components/card/next-action.fixture.html?board&long");
    const display = page.locator("[data-board-next-action] p");
    await expect(display).toHaveAttribute("title", "Long authored action ".repeat(50));
    await expect(display).toHaveAccessibleName(`Next action: ${"Long authored action ".repeat(50)}`.trim());
    expect(await display.evaluate((element) => element.getBoundingClientRect().height)).toBeLessThan(40);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
