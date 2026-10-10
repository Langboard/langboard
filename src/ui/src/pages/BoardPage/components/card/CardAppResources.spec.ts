import { test, expect } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/card/CardAppResources.fixture.html";
for (const width of [390, 1440]) {
    test(`explicit selection and cleanup at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(fixture);
        await expect(page.getByRole("button", { name: "App resources", exact: true })).toBeVisible();
        expect(await page.evaluate(() => (window as any).resourceCalls.length)).toBe(0);
        await page.getByRole("button", { name: "App resources", exact: true }).click();
        await page.getByRole("combobox").selectOption("00000000002");
        await page.getByRole("checkbox", { name: "Example issue" }).check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByText("Selected resources: 1/20")).toBeVisible();
        await page.getByRole("button", { name: "Clear selection", exact: true }).click();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByText("Selected resources: 0/20")).toBeVisible();
        expect(
            await page.evaluate(() => (window as any).resourceCalls.filter((call: any) => call.method === "put").map((call: any) => call.data))
        ).toEqual([
            { resource_uids: ["00000000001"], expected_revision: null },
            { resource_uids: [], expected_revision: 1 },
        ]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
}
test("unknown save outcome blocks replay until readback", async ({ page }) => {
    await page.goto(`${fixture}?lost`);
    await page.getByRole("button", { name: "App resources", exact: true }).click();
    await page.getByRole("combobox").selectOption("00000000002");
    await page.getByRole("checkbox", { name: "Example issue" }).check();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("checkbox", { name: "Example issue" })).toBeChecked();
    expect(await page.evaluate(() => (window as any).resourceCalls.filter((call: any) => call.method === "put").length)).toBe(1);
});

test("revoked connection remains discoverable for cleanup on reopen", async ({ page }) => {
    await page.goto(`${fixture}?revoked`);
    await page.getByRole("button", { name: "App resources", exact: true }).click();
    await page.getByRole("combobox").selectOption("00000000002");
    await expect(page.getByText("Selected resources: 1/20")).toBeVisible();
    await expect(page.getByRole("checkbox")).toHaveCount(0);
    await page.getByRole("button", { name: "Clear selection", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Selected resources: 0/20")).toBeVisible();
    expect(
        await page.evaluate(() => (window as any).resourceCalls.filter((call: any) => call.method === "put").map((call: any) => call.data))
    ).toEqual([{ resource_uids: [], expected_revision: 3 }]);
});
