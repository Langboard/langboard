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
        if (width === 390) await page.screenshot({ path: "/tmp/langboard-project-collection-390.png", fullPage: true });
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Could not save");
        await expect(page.locator("output")).toHaveAttribute("data-busy", "false");
        await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toBeChecked();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(creates).toBe(1);
        expect(puts).toBe(2);
        expect(saved.collections).toEqual([
            { uid: expect.any(String), name: "ERP project", board_uids: ["00000000001", "00000000002", "00000000003"], starred: false },
        ]);
        await page.reload();
        await expect(page.locator("output")).toContainText("ERP project");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

for (const createBoard of [false, true]) {
    test(`canceling collection does not save a bundle after source creation: ${createBoard}`, async ({ page }) => {
        let puts = 0;
        let creates = 0;
        await page.route("**/account/project-collections", (route) => {
            if (route.request().method() !== "GET") puts++;
            return route.fulfill({ json: { revision: 0, collections: [] } });
        });
        await page.route("**/settings/project-templates", (route) => route.fulfill({ json: { templates: [] } }));
        await page.route("**/dashboard/projects/new", (route) => {
            creates++;
            return route.fulfill({ json: { project_uid: "00000000003" } });
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        await page.getByLabel("Project name", { exact: true }).fill("Canceled draft");
        await page.getByRole("checkbox", { name: "ERP board", exact: true }).click();
        await page.getByRole("button", { name: "Create source board", exact: true }).click();
        const sourceDialog = page.getByRole("dialog").last();
        if (createBoard) {
            await sourceDialog.locator("input[name=title]").fill("Created source");
            await sourceDialog.getByRole("combobox").first().fill("Other");
            await sourceDialog.getByRole("button", { name: "Create", exact: true }).click();
            await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toBeChecked();
        } else {
            await sourceDialog.getByRole("button", { name: "Cancel", exact: true }).click();
        }
        await expect(page.getByRole("dialog")).toHaveCount(1);
        await page.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        await expect(page.getByLabel("Project name", { exact: true })).toHaveValue("");
        await expect(page.getByRole("checkbox", { name: "ERP board", exact: true })).not.toBeChecked();
        if (createBoard) await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).not.toBeChecked();
        expect(creates).toBe(createBoard ? 1 : 0);
        expect(puts).toBe(0);
        await expect(page.locator("output")).toHaveText("[]");
    });
}

test("a revision conflict refreshes existing collections before retrying the same draft", async ({ page }) => {
    const other = { uid: "00000000-0000-4000-8000-000000000001", name: "Other device", board_uids: ["00000000002"] };
    let saved = { revision: 0, collections: [] as object[] };
    let puts = 0;
    await page.route("**/account/project-collections", (route) => {
        if (route.request().method() === "GET") return route.fulfill({ json: saved });
        const input = route.request().postDataJSON();
        if (++puts === 1) {
            expect(input.expected_revision).toBe(0);
            saved = { revision: 1, collections: [other] };
            return route.fulfill({ status: 409, json: {} });
        }
        expect(input.expected_revision).toBe(1);
        saved = { revision: 2, collections: input.collections };
        return route.fulfill({ json: saved });
    });
    await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
    await page.getByRole("button", { name: "Add collection", exact: true }).click();
    await page.getByLabel("Project name", { exact: true }).fill("My draft");
    await page.getByRole("checkbox", { name: "ERP board", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Could not save");
    await expect(page.locator("output")).toContainText("Other device");
    await expect(page.locator("output")).toHaveAttribute("data-busy", "false");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(saved.collections).toEqual([
        { ...other, starred: false },
        { uid: expect.any(String), name: "My draft", board_uids: ["00000000001"], starred: false },
    ]);
    expect(puts).toBe(2);
});

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

test("closing a pending source dialog prevents its response from changing the reopened dialog", async ({ page }) => {
    let pending: import("@playwright/test").Route | undefined;
    await page.route("**/account/project-collections", (route) => route.fulfill({ json: { revision: 0, collections: [] } }));
    await page.route("**/settings/project-templates", (route) => route.fulfill({ json: { templates: [] } }));
    await page.route("**/dashboard/projects/new", (route) => {
        pending = route;
    });
    await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
    await page.getByRole("button", { name: "Add collection", exact: true }).click();
    await page.getByRole("button", { name: "Create source board", exact: true }).click();
    const sourceDialog = page.getByRole("dialog").last();
    await sourceDialog.locator("input[name=title]").fill("Old source");
    await sourceDialog.getByRole("combobox").first().fill("Other");
    await sourceDialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect.poll(() => !!pending).toBe(true);
    await sourceDialog.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "Create source board", exact: true }).click();
    await expect(sourceDialog.locator("input[name=title]")).toBeDisabled();
    await expect(sourceDialog.locator("button[type=submit]")).toBeDisabled();
    await pending!.fulfill({ json: { project_uid: "00000000003" } });
    await expect(sourceDialog.locator("input[name=title]")).toBeEnabled();
    await sourceDialog.locator("input[name=title]").fill("New source draft");
    await expect(sourceDialog.locator("input[name=title]")).toHaveValue("New source draft");
    await sourceDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toHaveCount(0);
    await expect(page.locator("output")).toHaveText("[]");
});

for (const action of ["close parent", "switch account"] as const) {
    test(`a late source response after ${action} cannot change a new collection draft`, async ({ page }) => {
        let pending: import("@playwright/test").Route | undefined;
        let writes = 0;
        await page.route("**/account/project-collections", (route) => {
            if (route.request().method() !== "GET") writes++;
            return route.fulfill({ json: { revision: 0, collections: [] } });
        });
        await page.route("**/settings/project-templates", (route) => route.fulfill({ json: { templates: [] } }));
        await page.route("**/dashboard/projects/new", (route) => {
            pending = route;
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        await page.getByRole("button", { name: "Create source board", exact: true }).click();
        const sourceDialog = page.getByRole("dialog").last();
        await sourceDialog.locator("input[name=title]").fill("Old account source");
        await sourceDialog.getByRole("combobox").first().fill("Other");
        await sourceDialog.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => !!pending).toBe(true);
        if (action === "switch account") {
            // Account changes originate outside the modal, such as another authenticated surface.
            await page
                .getByRole("button", { name: "Switch account", exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
        } else {
            await sourceDialog.getByRole("button", { name: "Close", exact: true }).click();
            await page.getByRole("button", { name: "Cancel", exact: true }).click();
        }
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        await page.getByLabel("Project name", { exact: true }).fill("Fresh draft");
        const response = page.waitForResponse("**/dashboard/projects/new");
        await pending!.fulfill({ json: { project_uid: "00000000003" } });
        await response;
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await expect(page.getByLabel("Project name", { exact: true })).toHaveValue("Fresh draft");
        await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
        await expect(page.locator("[data-path]")).toHaveText("/");
        expect(writes).toBe(0);
    });
}

for (const action of ["close", "switch account", "complete"] as const) {
    test(`native board creation ${action} preserves navigation lifetime`, async ({ page }) => {
        let pending: import("@playwright/test").Route | undefined;
        await page.route("**/account/project-collections", (route) => route.fulfill({ json: { revision: 0, collections: [] } }));
        await page.route("**/settings/project-templates", (route) => route.fulfill({ json: { templates: [] } }));
        await page.route("**/dashboard/projects/new", (route) => {
            pending = route;
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html?native&keep-open-on-account-switch");
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        const sourceDialog = page.getByRole("dialog");
        await sourceDialog.locator("input[name=title]").fill("Native source");
        await sourceDialog.getByRole("combobox").first().fill("Other");
        await sourceDialog.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => !!pending).toBe(true);
        if (action === "close") await sourceDialog.getByRole("button", { name: "Close", exact: true }).click();
        if (action === "switch account") {
            await page
                .getByRole("button", { name: "Switch account", exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
        }
        const response = page.waitForResponse("**/dashboard/projects/new");
        await pending!.fulfill({ json: { project_uid: "00000000003" } });
        await response;
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await expect(page.locator("[data-path]")).toHaveText(action === "complete" ? /00000000003/ : "/");
        if (action === "switch account") {
            await expect(sourceDialog.locator("input[name=title]")).toBeEnabled();
            await expect(sourceDialog.locator("input[name=title]")).toHaveValue("");
        }
    });
}

for (const width of [1280, 390]) {
    test(`native creation retry preserves draft UUID and new draft changes it at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        const requests: { request_uuid: string; title: string }[] = [];
        await page.route("**/account/project-collections", (route) => route.fulfill({ json: { revision: 0, collections: [] } }));
        await page.route("**/settings/project-templates", (route) => route.fulfill({ json: { templates: [] } }));
        await page.route("**/dashboard/projects/new", (route) => {
            requests.push(route.request().postDataJSON());
            // The server committed the first request, but its response was lost.
            if (requests.length === 1) return route.abort("failed");
            return route.fulfill({ json: { project_uid: "00000000003" } });
        });
        await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
        await page.getByRole("button", { name: "Add collection", exact: true }).click();
        await page.getByRole("button", { name: "Create source board", exact: true }).click();
        let source = page.getByRole("dialog").last();
        await source.locator("input[name=title]").fill("Retained native source");
        await source.getByRole("combobox").first().fill("Other");
        await source.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => requests.length).toBe(1);
        await expect(source.getByRole("button", { name: "Create", exact: true })).toBeEnabled();
        await source.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => requests.length).toBe(2);
        expect(requests[0].request_uuid).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        expect(requests[1]).toEqual(requests[0]);
        await expect(page.getByRole("checkbox", { name: "Created source", exact: true })).toBeChecked();
        await page.getByRole("button", { name: "Create source board", exact: true }).click();
        source = page.getByRole("dialog").last();
        await source.locator("input[name=title]").fill("A new creation draft");
        await source.getByRole("combobox").first().fill("Other");
        await source.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => requests.length).toBe(3);
        expect(requests[2].request_uuid).not.toBe(requests[0].request_uuid);
    });
}

test("late collection save stays isolated after account switch", async ({ page }) => {
    let pending: import("@playwright/test").Route | undefined;
    await page.route("**/account/project-collections", (route) => {
        if (route.request().method() === "PUT") {
            pending = route;
            return;
        }
        return route.fulfill({ json: { revision: 0, collections: [] } });
    });
    await page.goto("/src/pages/DashboardPage/components/ProjectCollection.fixture.html");
    await page.getByRole("button", { name: "Add collection", exact: true }).click();
    await page.getByLabel("Project name", { exact: true }).fill("Old account private collection");
    await page.getByRole("checkbox", { name: "ERP board", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => !!pending).toBe(true);
    await page
        .getByRole("button", { name: "Switch account", exact: true, includeHidden: true })
        .evaluate((button: HTMLButtonElement) => button.click());
    await expect(page.locator("output")).toHaveText("[]");
    const response = page.waitForResponse("**/account/project-collections");
    await pending!.fulfill({ json: { revision: 1, collections: pending!.request().postDataJSON().collections } });
    await response;
    await expect(page.getByRole("button", { name: "Add collection", exact: true })).toBeEnabled();
    await expect(page.locator("output")).toHaveText("[]");
    await page.getByRole("button", { name: "Add collection", exact: true }).click();
    await expect(page.getByLabel("Project name", { exact: true })).toHaveValue("");
    await expect(page.getByText("Old account private collection", { exact: true })).toHaveCount(0);
});
