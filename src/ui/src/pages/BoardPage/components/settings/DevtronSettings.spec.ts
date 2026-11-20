import { test, expect, type Page } from "@playwright/test";
const path = "/src/pages/BoardPage/components/settings/DevtronSettings.fixture.html";
const writes = (page: Page) =>
    page.evaluate(
        () =>
            (
                window as unknown as {
                    devtronWrites: {
                        method: string;
                        url: string;
                        body: Record<string, unknown>;
                    }[];
                }
            ).devtronWrites
    );
async function register(page: Page) {
    await page.getByLabel("Devtron URL", { exact: true }).fill("https://devtron.example.invalid");
    await page.getByLabel("Stored credential reference").fill("secret://ref/secret00001");
    await page.getByRole("button", { name: "Review connection", exact: true }).click();
}
async function selectNative(page: Page) {
    await page.getByLabel("Application ID", { exact: true }).fill("17");
    await page.getByLabel("Environment ID", { exact: true }).fill("4");
    await page.getByRole("button", { name: "Review deployment selection" }).click();
    await page.getByRole("button", { name: "Select this deployment", exact: true }).click();
}
for (const width of [1440, 390]) {
    test(`explicit registration, selection, read verification and withdrawal at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(path);
        await register(page);
        expect(await writes(page)).toEqual([]);
        await page.getByRole("button", { name: "Connect account", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Deployment access" })).toBeVisible();
        const registration = (await writes(page))[0];
        expect(registration.body).not.toHaveProperty("deployment_scope");
        await selectNative(page);
        await expect(page.getByText("app-17-env-4 · Unknown", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Verify read access", exact: true }).click();
        expect((await writes(page)).filter((row) => row.url.endsWith("/verify"))).toHaveLength(0);
        await page.getByRole("button", { name: "Verify this deployment", exact: true }).click();
        await expect(page.getByText("Read access verified. Service health has not been checked.")).toBeVisible();
        await page.getByRole("button", { name: "Remove selection", exact: true }).click();
        await page.getByRole("button", { name: "Remove this selection", exact: true }).click();
        expect((await writes(page)).filter((row) => row.method === "put").map((row) => row.body.selected)).toEqual([true, false]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
}
test("unknown selection outcome blocks replay until explicit reconciliation", async ({ page }) => {
    await page.goto(`${path}?connected&unknown`);
    await page.getByRole("combobox", { name: "Connected account", exact: true }).selectOption("connection1");
    await selectNative(page);
    await expect(page.getByRole("alert").first()).toBeVisible();
    expect((await writes(page)).filter((row) => row.method === "put")).toHaveLength(1);
    await page.getByRole("button", { name: "Refresh current state", exact: true }).first().click();
    await expect(page.getByText("app-17-env-4 · Unknown", { exact: true })).toBeVisible();
    expect((await writes(page)).filter((row) => row.method === "put")).toHaveLength(1);
});
for (const condition of ["stale", "deny"]) {
    test(`${condition} authority at confirmation prevents registration`, async ({ page }) => {
        await page.goto(`${path}?${condition}`);
        await register(page);
        await page.getByRole("button", { name: "Connect account", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        expect(await writes(page)).toEqual([]);
        await expect(page.getByRole("button", { name: "Connect account", exact: true })).toHaveCount(0);
    });
}
test("read-only operator does not load private account metadata", async ({ page }) => {
    await page.goto(`${path}?readonly`);
    await expect(page.getByText("Board update permission is required to manage Devtron connections.")).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { devtronReads: string[] }).devtronReads)).toEqual([]);
    expect(await writes(page)).toEqual([]);
});
test("disconnect requires the explicit all-board confirmation", async ({ page }) => {
    await page.goto(`${path}?connected`);
    await page.getByRole("combobox", { name: "Connected account", exact: true }).selectOption("connection1");
    await page.getByRole("button", { name: "Disconnect account", exact: true }).click();
    expect(await writes(page)).toEqual([]);
    await expect(page.getByText(/revoke its resource access on every linked board/)).toBeVisible();
    await page.getByRole("button", { name: "Disconnect on all boards", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Connected account", exact: true }).locator("option:checked")).toContainText("Disconnected");
    expect((await writes(page)).filter((row) => row.url.endsWith("/disconnect"))).toHaveLength(1);
});
