import { expect, test } from "@playwright/test";
const fixture = "/src/components/Layout/automation-runs.fixture.html";
const control = "http://127.0.0.1:5387/control";
for (const width of [390, 1440]) {
    test.describe(`automation HTTP boundaries ${width}`, () => {
        test.use({ viewport: { width, height: 900 } });
        test.beforeEach(async ({ page, request }) => {
            await request.get(`${control}?reset`);
            page.on("pageerror", (error) => {
                throw error;
            });
        });
        test("lazy metadata, explicit detail, paging, and keyboard central dialog", async ({ page, request }) => {
            await page.goto(fixture);
            expect((await (await request.get(control)).json()).calls).toEqual([]);
            await page.getByRole("button", { name: "Toggle automation" }).press("Enter");
            await expect(page.getByText("Dispatch accepted", { exact: true })).toBeVisible();
            let state = await (await request.get(control)).json();
            expect(state.calls).toEqual(["/board/left/automation/runs"]);
            await expect(page.getByText(/PRIVATE DETAIL/)).toHaveCount(0);
            await page.getByRole("button", { name: "More runs" }).click();
            await expect(page.getByRole("button", { name: "View run details" })).toHaveCount(2);
            state = await (await request.get(control)).json();
            expect(new URL(state.calls[1], control).searchParams.get("before")).toBe("chat:fixturepage");
            await page.getByRole("button", { name: "View run details" }).first().press("Enter");
            await expect(page.getByRole("dialog")).toBeVisible();
            await expect(page.getByText("PRIVATE DETAIL left 0")).toBeVisible();
            await expect(page.locator("[data-dialog-overlay=true]")).toHaveCSS("position", "fixed");
            await expect(page.locator("section [role=dialog]")).toHaveCount(0);
            await expect(page.getByRole("dialog").locator("..")).toHaveCSS("opacity", "1");
            await page.screenshot({ path: `../../.verification/mcp-execution-history/details-${width}.png` });
            await page.keyboard.press("Escape");
            await expect(page.getByRole("dialog")).toHaveCount(0);
            expect((await (await request.get(control)).json()).calls).toHaveLength(3);
        });
        test("MCP source shows truthful outcome and payload-free explicit details", async ({ page, request }) => {
            await request.get(`${control}?mcp`);
            await page.goto(fixture);
            await page.getByRole("button", { name: "Toggle automation" }).click();
            await expect(page.getByText("Native MCP tool", { exact: true })).toBeVisible();
            await expect(page.getByText("Outcome unknown", { exact: true })).toBeVisible();
            await expect(page.getByText("patch_card_description", { exact: true })).toHaveCount(0);
            await page.getByRole("button", { name: "View run details" }).click();
            const dialog = page.getByRole("dialog");
            await expect(dialog.getByText("patch_card_description", { exact: true })).toBeVisible();
            await expect(dialog.getByText("MCP metadata only.", { exact: false })).toBeVisible();
            await expect(dialog.getByText(/PRIVATE DETAIL/)).toHaveCount(0);
            expect((await (await request.get(control)).json()).calls).toHaveLength(2);
        });
        test("loading, actual HTTP failure, explicit retry, and empty state", async ({ page, request }) => {
            await request.get(`${control}?hold&fail`);
            await page.goto(fixture);
            await page.getByRole("button", { name: "Toggle automation" }).click();
            await expect(page.getByRole("status")).toHaveText("Loading...");
            await request.get(`${control}?release`);
            await expect(page.getByRole("alert")).toContainText("Could not load automation runs");
            expect((await (await request.get(control)).json()).calls).toHaveLength(1);
            await request.get(`${control}?empty`);
            await page.getByRole("button", { name: "Retry", exact: true }).click();
            await expect(page.getByText("No authorized automation runs")).toBeVisible();
        });
        test("failed refetch hides cached detail and metadata until explicit retry", async ({ page, request }) => {
            await page.goto(fixture);
            await page.getByRole("button", { name: "Toggle automation" }).click();
            await page.getByRole("button", { name: "View run details" }).click();
            const dialog = page.getByRole("dialog");
            await expect(dialog.getByText("PRIVATE DETAIL left 0")).toBeVisible();
            await request.get(`${control}?fail`);
            await dialog.getByRole("button", { name: "Refresh", exact: true }).click();
            await expect(dialog.getByRole("alert")).toBeVisible();
            await expect(dialog.getByText(/PRIVATE DETAIL/)).toHaveCount(0);
            await dialog.getByRole("button", { name: "Retry", exact: true }).click();
            await expect(dialog.getByText("PRIVATE DETAIL left 0")).toBeVisible();
            await page.keyboard.press("Escape");
            await request.get(`${control}?fail`);
            await page.getByRole("button", { name: "Refresh", exact: true }).click();
            await expect(page.getByRole("alert")).toBeVisible();
            await expect(page.getByRole("button", { name: "View run details" })).toHaveCount(0);
            await page.getByRole("button", { name: "Retry", exact: true }).click();
            await expect(page.getByRole("button", { name: "View run details" })).toBeVisible();
        });
        for (const transition of ["actor", "session", "board", "revoke", "permissions"]) {
            test(`late summary discarded after ${transition}`, async ({ page, request }) => {
                await request.get(`${control}?hold`);
                await page.goto(fixture);
                await page.getByRole("button", { name: "Toggle automation" }).click();
                await expect(page.getByRole("status")).toBeVisible();
                await expect.poll(async () => (await (await request.get(control)).json()).pending).toBe(1);
                await page.getByRole("button", { name: transition === "revoke" ? "Revoke" : `Switch ${transition}`, exact: true }).click();
                if (transition !== "revoke") await expect.poll(async () => (await (await request.get(control)).json()).pending).toBe(2);
                await request.get(`${control}?release`);
                if (transition === "revoke") await expect(page.getByRole("region", { name: "Recent automation runs" })).toHaveCount(0);
                else {
                    await expect(page.getByRole("button", { name: "View run details" })).toHaveCount(1);
                    await expect(page.getByText("Completed", { exact: true })).toBeVisible();
                    await expect(page.getByText("Failed", { exact: true })).toHaveCount(0);
                }
                await expect(page.getByText(/PRIVATE DETAIL/)).toHaveCount(0);
            });
            test(`late detail discarded after ${transition}`, async ({ page, request }) => {
                await page.goto(fixture);
                await page.getByRole("button", { name: "Toggle automation" }).click();
                await expect(page.getByRole("button", { name: "View run details" })).toBeVisible();
                await request.get(`${control}?hold`);
                await page.getByRole("button", { name: "View run details" }).click();
                await expect(page.getByRole("dialog")).toBeVisible();
                await expect.poll(async () => (await (await request.get(control)).json()).pending).toBe(1);
                // Native dialog is modal; invoke the external auth/model transition.
                await page
                    .getByRole("button", { name: transition === "revoke" ? "Revoke" : `Switch ${transition}`, exact: true, includeHidden: true })
                    .evaluate((button: HTMLButtonElement) => button.click());
                await request.get(`${control}?release`);
                await expect(page.getByRole("dialog")).toHaveCount(0);
                await expect(page.getByText(/PRIVATE DETAIL/)).toHaveCount(0);
            });
        }
    });
}
