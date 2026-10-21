import { expect, test } from "@playwright/test";

for (const [width, commitBeforeFailure] of [
    [1280, false],
    [390, true],
] as const) {
    test(`collection selection persists and server failure retains created board at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        let saved = { revision: 0, collections: [] as object[] };
        let puts = 0;
        let creates = 0;
        await page.route("**/account/project-collections", async (route) => {
            if (route.request().method() === "GET") return route.fulfill({ json: saved });
            puts++;
            const input = route.request().postDataJSON();
            if (puts === 1) {
                if (commitBeforeFailure) saved = { revision: saved.revision + 1, collections: input.collections };
                return route.fulfill({ status: 500, json: { message: "temporary failure" } });
            }
            expect(input.expected_revision).toBe(saved.revision);
            saved = { revision: saved.revision + 1, collections: input.collections };
            return route.fulfill({ json: saved });
        });
        await page.route("**/settings/project-templates", (route) => route.fulfill({ json: { templates: [] } }));
        await page.route("**/dashboard/projects/new", (route) => {
            creates++;
            return route.fulfill({ json: { project_uid: "00000000003" } });
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        await page.getByLabel("Project name", { exact: true }).fill("ERP project");
        await page.getByRole("checkbox", { name: "ERP board", exact: true }).click();
        await page.getByRole("checkbox", { name: "Operations board", exact: true }).click();
        await page.getByRole("button", { name: "Create source board", exact: true }).click();
        const createDialog = page.getByRole("dialog").last();
        await createDialog.locator("input[name=title]").fill("Created source");
        await createDialog.getByRole("combobox").first().fill("Other");
        await createDialog.getByRole("button", { name: "Create", exact: true }).click();
        await expect(page.getByRole("dialog")).toHaveCount(1);
        await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toBeChecked();
        await expect(page.getByLabel("Project name", { exact: true })).toHaveValue("ERP project");
        if (width === 390) await page.screenshot({ path: ".verification/project-collection-390.png", fullPage: true });
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Could not save");
        await expect(page.locator("output")).toHaveAttribute("data-busy", "false");
        await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toBeChecked();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(creates).toBe(1);
        expect(puts).toBe(2);
        expect(saved.collections).toEqual([
            { uid: expect.any(String), name: "ERP project", board_uids: ["00000000001", "00000000002", "00000000003"] },
        ]);
        await page.reload();
        await expect(page.locator("output")).toContainText("ERP project");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("late collection response does not cross an account switch", async ({ page }) => {
    let first: import("@playwright/test").Route | undefined;
    let gets = 0;
    await page.route("**/account/project-collections", (route) => {
        gets++;
        if (gets === 1) {
            first = route;
            return;
        }
        return route.fulfill({ json: { revision: 0, collections: [] } });
    });
    await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
    await expect.poll(() => !!first).toBe(true);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByRole("button", { name: "Add collection", exact: true })).toBeEnabled();
    await first!.fulfill({
        json: { revision: 1, collections: [{ uid: "00000000-0000-4000-8000-000000000001", name: "Private old group", board_uids: [] }] },
    });
    await expect(page.locator("output")).toHaveText(JSON.stringify([]));
    await expect(page.getByText("Private old group")).toHaveCount(0);
});
