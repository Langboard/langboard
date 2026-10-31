import { expect, test, type Page } from "@playwright/test";
declare global {
    interface Window {
        cardCreationFixture: {
            requests: Array<{ payload: Record<string, unknown> }>;
            release: (index: number, success: boolean) => void;
        };
        composerFixture: { saveDescriptionDraft: (body: string) => void };
    }
}
const fixture = "/src/core/providers/BoardAddCardProvider.fixture.html";
const key = "lb-draft-v1:actor-a:project-a:card-new:column-a";
const body = "  ## Exact body\n\n한글 **bold**\n- [ ] task\n\n";
const title = (page: Page) => page.getByRole("textbox", { name: "Enter a title", exact: true });
const description = (page: Page) => page.getByRole("textbox", { name: "Description (optional)", exact: true });
async function start(page: Page) {
    await page.goto(fixture);
    await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toBeVisible();
}
async function fill(page: Page, text = "Draft title", content = body) {
    await title(page).fill(text);
    await page.locator("summary").click();
    await description(page).fill(content);
}
async function stored(page: Page, scope = key) {
    return page.evaluate((key) => JSON.parse(localStorage.getItem(key) ?? "null"), scope);
}
async function submit(page: Page) {
    await title(page).press("Enter");
    await expect.poll(() => page.evaluate(() => window.cardCreationFixture.requests.length)).toBeGreaterThan(0);
}
async function release(page: Page, index: number, success: boolean) {
    await page.evaluate(({ index, success }) => window.cardCreationFixture.release(index, success), { index, success });
}

test("native payload preserves exact multiline body; matching success clears draft", async ({ page }) => {
    await start(page);
    await fill(page);
    await description(page).press("Enter");
    await expect(description(page)).toHaveValue(body + "\n");
    expect(await page.evaluate(() => window.cardCreationFixture.requests.length)).toBe(0);
    await description(page).press("Backspace");
    await page.getByRole("button", { name: "Add card", exact: true }).click();
    await expect.poll(() => page.evaluate(() => window.cardCreationFixture.requests.length)).toBe(1);
    const payload = await page.evaluate(() => window.cardCreationFixture.requests[0].payload);
    expect(payload).toEqual({ project_column_uid: "column-a", title: "Draft title", description: { content: body } });
    await release(page, 0, true);
    await expect.poll(() => stored(page)).toBeNull();
    await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toHaveValue("");
});

test("pagehide and reload restore both fields at desktop, medium and mobile widths", async ({ page }) => {
    await start(page);
    await fill(page);
    await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
    expect(await stored(page)).toMatchObject({ content: "Draft title", description: body });
    await page.reload();
    await page.getByRole("button", { name: "Add a card", exact: true }).click();
    for (const width of [1440, 900, 390]) {
        await page.setViewportSize({ width, height: 800 });
        await expect(title(page)).toHaveValue("Draft title");
        await expect(description(page)).toHaveValue(body);
        const bounds = await description(page).boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        expect(bounds!.height).toBeLessThanOrEqual(192);
    }
});

test("unmount flushes immediate edits and remount restores both fields", async ({ page }) => {
    await start(page);
    await fill(page);
    await page.getByRole("button", { name: "Toggle composer", exact: true }).click();
    expect(await stored(page)).toMatchObject({ content: "Draft title", description: body });
    await page.getByRole("button", { name: "Toggle composer", exact: true }).click();
    if (!(await title(page).isVisible())) await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toHaveValue("Draft title");
    await expect(description(page)).toHaveValue(body);
});

test("legacy JSON-looking title stays a title; title-only Enter omits description", async ({ page }) => {
    await page.goto(fixture);
    const legacy = JSON.stringify({ description: "literal title" });
    await page.evaluate(({ key, legacy }) => localStorage.setItem(key, JSON.stringify({ content: legacy, savedAt: Date.now() })), { key, legacy });
    await page.reload();
    await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toHaveValue(legacy);
    await expect(description(page)).toBeHidden();
    await submit(page);
    const payload = await page.evaluate(() => window.cardCreationFixture.requests[0].payload);
    expect(payload.title).toBe(legacy);
    expect(payload).not.toHaveProperty("description");
    await release(page, 0, true);
});

test("failed transport keeps title and body and allows retry", async ({ page }) => {
    await start(page);
    await fill(page);
    await submit(page);
    await release(page, 0, false);
    await expect(title(page)).toBeEnabled();
    await expect(title(page)).toHaveValue("Draft title");
    await expect(description(page)).toHaveValue(body);
    expect(await stored(page)).toMatchObject({ content: "Draft title", description: body });
    await page.reload();
    await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(description(page)).toHaveValue(body);
});

test("later input survives older success without closing composer", async ({ page }) => {
    await start(page);
    await fill(page);
    await submit(page);
    await page.evaluate(() => window.composerFixture.saveDescriptionDraft("newer body"));
    await release(page, 0, true);
    await expect(description(page)).toBeEnabled();
    await expect(description(page)).toHaveValue("newer body");
    expect(await stored(page)).toMatchObject({ description: "newer body" });
});

test("reopened session and later request survive old response", async ({ page }) => {
    await start(page);
    await fill(page);
    await submit(page);
    await page.getByRole("button", { name: "Reopen session", exact: true }).click();
    await expect(title(page)).toBeEnabled();
    await title(page).fill("New session");
    await title(page).press("Enter");
    await expect.poll(() => page.evaluate(() => window.cardCreationFixture.requests.length)).toBe(2);
    await release(page, 0, true);
    await expect(description(page)).toBeDisabled();
    expect(await stored(page)).toMatchObject({ content: "New session", description: body });
    await release(page, 1, false);
    await expect(title(page)).toBeEnabled();
    await expect(title(page)).toHaveValue("New session");
});

test("account switch preserves isolated drafts and older account success cannot clear current draft", async ({ page }) => {
    await start(page);
    await fill(page);
    const oldDescriptionWriter = await page.evaluateHandle(() => window.composerFixture.saveDescriptionDraft);
    await submit(page);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await oldDescriptionWriter.evaluate((save) => save("Stale account body"));
    await expect(title(page)).toHaveValue("");
    await expect(description(page)).toBeHidden();
    await title(page).fill("Actor B title");
    await page.locator("summary").click();
    await description(page).fill("Actor B body");
    await release(page, 0, true);
    await expect(title(page)).toHaveValue("Actor B title");
    await expect(description(page)).toHaveValue("Actor B body");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(title(page)).toHaveValue("Draft title");
    await expect(description(page)).toHaveValue(body);
});

test("project and column transitions flush and restore their own drafts", async ({ page }) => {
    await start(page);
    await fill(page);
    for (const boundary of ["Switch project", "Switch column"]) {
        await page.getByRole("button", { name: boundary, exact: true }).click();
        if (!(await title(page).isVisible())) await page.getByRole("button", { name: "Add a card", exact: true }).click();
        await expect(title(page)).toHaveValue("");
        await title(page).fill("Other scope");
        await page.getByRole("button", { name: boundary, exact: true }).click();
        if (!(await title(page).isVisible())) await page.getByRole("button", { name: "Add a card", exact: true }).click();
        await expect(title(page)).toHaveValue("Draft title");
        await expect(description(page)).toHaveValue(body);
    }
});

test("delayed success after column switch clears only submitted draft and preserves current composer", async ({ page }) => {
    const columnBKey = "lb-draft-v1:actor-a:project-a:card-new:column-b";
    await start(page);
    await fill(page, "Submitted column A", "A body\n한글");
    await submit(page);
    expect(await page.evaluate(() => window.cardCreationFixture.requests[0].payload)).toEqual({
        project_column_uid: "column-a",
        title: "Submitted column A",
        description: { content: "A body\n한글" },
    });
    expect(await stored(page)).toMatchObject({ content: "Submitted column A", description: "A body\n한글" });
    await page.getByRole("button", { name: "Switch column", exact: true }).click();
    if (!(await title(page).isVisible())) await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toBeEnabled();
    await expect(title(page)).toHaveValue("");
    await expect(description(page)).toBeHidden();
    await fill(page, "Unsubmitted column B");
    await release(page, 0, true);
    await expect.poll(() => stored(page)).toBeNull();
    await expect(title(page)).toBeEnabled();
    await expect(title(page)).toHaveValue("Unsubmitted column B");
    await expect(description(page)).toBeEnabled();
    await expect(description(page)).toHaveValue(body);
    expect(await stored(page, columnBKey)).toMatchObject({ content: "Unsubmitted column B", description: body });
    await page.getByRole("button", { name: "Switch column", exact: true }).click();
    if (!(await title(page).isVisible())) await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toHaveValue("");
    await expect(description(page)).toBeHidden();
    await page.getByRole("button", { name: "Switch column", exact: true }).click();
    if (!(await title(page).isVisible())) await page.getByRole("button", { name: "Add a card", exact: true }).click();
    await expect(title(page)).toHaveValue("Unsubmitted column B");
    await expect(description(page)).toHaveValue(body);
    expect(await page.evaluate(() => window.cardCreationFixture.requests.length)).toBe(1);
});

test("same-account reauthentication invalidates old success ownership", async ({ page }) => {
    await start(page);
    await fill(page);
    await submit(page);
    await page.getByRole("button", { name: "Reauthenticate", exact: true }).click();
    await expect(title(page)).toBeEnabled();
    await release(page, 0, true);
    await expect(title(page)).toHaveValue("Draft title");
    await expect(description(page)).toHaveValue(body);
    expect(await stored(page)).toMatchObject({ content: "Draft title", description: body });
});
