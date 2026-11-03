import { expect, test, type Page, type Route } from "@playwright/test";

async function open(page: Page, query = "") {
    page.on("pageerror", (error) => console.log("Fixture page error:", error.message));
    page.on("console", (message) => {
        if (message.type() === "error") console.log("Fixture console:", message.text());
    });
    await page.goto(`http://127.0.0.1:4297/src/pages/BoardPage/components/card/fullEditSave.fixture.html${query}`);
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Description draft")).toBeVisible();
}
async function holdSave(page: Page) {
    let release!: () => void;
    let arrived!: (route: Route) => void;
    const pending = new Promise<Route>((resolve) => (arrived = resolve));
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/card/**", async (route) => {
        if (route.request().method() !== "PUT") return route.fulfill({ json: {} });
        arrived(route);
        await gate;
        await route.fulfill({ json: {} });
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const route = await pending;
    return { release, body: route.request().postDataJSON() };
}
test("delayed success preserves newer deadline and can submit it afterward", async ({ page }) => {
    await open(page);
    const { release, body } = await holdSave(page);
    expect(body.deadline_at).toBe("2030-06-15T12:00:00.000Z");
    await page.getByLabel("Deadline draft").fill("2030-06-16T13:00");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Deadline draft")).toHaveValue("2030-06-16T13:00");
    await page.unroute("**/card/**");
    await page.route("**/card/**", (route) => route.fulfill({ json: {} }));
    const next = page.waitForRequest((request) => request.method() === "PUT");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    expect((await next).postDataJSON().deadline_at).toBe("2030-06-16T13:00:00.000Z");
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
});
test("pending save preserves newer title", async ({ page }) => {
    await open(page);
    await page.getByText("Original title", { exact: true }).click();
    await page.getByLabel("Title draft").fill("Submitted title");
    const { release, body } = await holdSave(page);
    expect(body.title).toBe("Submitted title");
    await page.getByLabel("Title draft").fill("Newer title");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Title draft")).toHaveValue("Newer title");
    await expect(page.getByTestId("mode")).toHaveText("edit");
});
test("pending save preserves description before its debounce flush", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Submitted description");
    const { release, body } = await holdSave(page);
    expect(body.description.content).toBe("Submitted description");
    await page.getByLabel("Description draft").fill("Newer description");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Description draft")).toHaveValue("Newer description");
    await expect(page.getByTestId("body-draft")).toHaveText("Newer description");
});
test("Cancel and same-card re-entry preserve a replacement with identical deadline", async ({ page }) => {
    await open(page);
    const { release } = await holdSave(page);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByText("No deadline", { exact: true }).click();
    await page.getByLabel("Deadline draft").fill("2030-06-15T12:00");
    release();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Deadline draft")).toHaveValue("2030-06-15T12:00");
});
test("failure retains drafts; successful retry clears them", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Recoverable description");
    await page.route("**/card/**", (route) => route.fulfill({ status: 500, json: { detail: "Synthetic failure" } }));
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    await expect(page.getByLabel("Description draft")).toHaveValue("Recoverable description");
    await expect(page.getByTestId("body-draft")).toHaveText("Recoverable description");
    await page.unroute("**/card/**");
    await page.route("**/card/**", (route) => route.fulfill({ json: {} }));
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
    await expect(page.getByTestId("body-draft")).toHaveText("empty");
});
test("unsynchronized draft blocks PUT and preserves Cancel", async ({ page }) => {
    await open(page, "?unsynced");
    let writes = 0;
    await page.route("**/card/**", (route) => {
        if (route.request().method() === "PUT") writes++;
        return route.fulfill({ json: {} });
    });
    await expect(page.getByLabel("Deadline draft")).toBeDisabled();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("mode")).toHaveText("edit");
    expect(writes).toBe(0);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    await expect(page.getByLabel("Deadline draft")).toHaveValue("");
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
});

test("normal unchanged sections and whitespace title finish without PUT", async ({ page }) => {
    await open(page, "?unchanged");
    await page.getByText("Original title", { exact: true }).click();
    await page.getByLabel("Title draft").fill("  Original title  ");
    let writes = 0;
    await page.route("**/card/**", (route) => {
        if (route.request().method() === "PUT") writes++;
        return route.fulfill({ json: {} });
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    expect(writes).toBe(0);
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
});

test("Cancel during pending save remains cancelled after completion", async ({ page }) => {
    await open(page);
    const { release } = await holdSave(page);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("mode")).toHaveText("view");
    release();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await expect(page.getByTestId("flip-draft")).toHaveText("null");
    await expect(page.getByTestId("body-draft")).toHaveText("empty");
});
