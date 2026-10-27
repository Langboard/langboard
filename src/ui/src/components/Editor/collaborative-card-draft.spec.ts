import { expect, test, type Page } from "@playwright/test";

const fixture = "/src/components/Editor/collaborative-card-draft.fixture.html";
const cardUID = "collaborative-card-draft-card";
const draftKey = (user = "a", card = cardUID) =>
    `lb-draft-v1:collaborative-card-draft-user-${user}:collaborative-card-draft-project:card-edit:${card}`;
const editor = (page: Page) => page.locator("[data-card-description] [data-slate-editor]");
const pageErrors = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => {
    const errors: string[] = [];
    pageErrors.set(page, errors);
    page.on("pageerror", (error) => errors.push(error.message));
});
test.afterEach(({ page }) => {
    expect(pageErrors.get(page)).toEqual([]);
});
async function stored(page: Page, user = "a", card = cardUID) {
    return page.evaluate((key) => JSON.parse(localStorage.getItem(key) ?? "null")?.content ?? null, draftKey(user, card));
}
async function edit(page: Page, content: string) {
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(editor(page)).toHaveAttribute("contenteditable", "true");
    await expect(editor(page)).toHaveText(content);
}
async function open(page: Page, query = "") {
    await page.goto(`${fixture}${query}`);
    await edit(page, "Original description");
}
async function pauseDebounce(page: Page) {
    const now = Date.now();
    await page.clock.install({ time: now });
    await page.clock.pauseAt(now + 1000);
}
async function close(page: Page) {
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByText("Card closed", { exact: true })).toBeVisible();
}
async function reopen(page: Page, content: string) {
    await page.getByRole("button", { name: "Reopen card", exact: true }).click();
    await edit(page, content);
}

for (const persisted of [false, true]) {
    test(`native Plate Close retains ${persisted ? "persisted" : "pending"} draft through reopen and reload`, async ({ page }) => {
        await open(page);
        const content = persisted ? "Native persisted body" : "Native pending body";
        if (!persisted) await pauseDebounce(page);
        await editor(page).fill(content);
        await expect(editor(page)).toHaveText(content);
        if (persisted) await expect.poll(() => stored(page)).toBe(content);
        else expect(await stored(page)).toBeNull();
        await close(page);
        await expect.poll(() => stored(page)).toBe(content);
        await expect(page.getByTestId("writes")).toHaveText("0");
        if (!persisted) await page.clock.resume();
        await reopen(page, content);
        await page.reload();
        await edit(page, content);
    });
}

test("native delayed server snapshot preserves pending body and restored seed", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await editor(page).fill("Draft before delayed snapshot");
    await page.getByRole("button", { name: "Delayed server update", exact: true }).click();
    await expect(editor(page)).toHaveText("Draft before delayed snapshot");
    await close(page);
    await expect.poll(() => stored(page)).toBe("Draft before delayed snapshot");
    await page.clock.resume();
    await reopen(page, "Draft before delayed snapshot");
    await expect(page.getByTestId("writes")).toHaveText("0");
});

test("native explicit Cancel discards pending and persisted body", async ({ page }) => {
    await open(page);
    await editor(page).fill("Persisted discard");
    await expect.poll(() => stored(page)).toBe("Persisted discard");
    await pauseDebounce(page);
    await editor(page).fill("Pending discard");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    expect(await stored(page)).toBeNull();
    await page.clock.runFor(700);
    expect(await stored(page)).toBeNull();
    await page.clock.resume();
    await edit(page, "Original description");
});

test("native failed save and Close retain body; successful retry clears it", async ({ page }) => {
    await open(page);
    await editor(page).fill("Native failed request body");
    await page.getByRole("button", { name: "Toggle save failure", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect.poll(() => stored(page)).toBe("Native failed request body");
    await close(page);
    await expect.poll(() => stored(page)).toBe("Native failed request body");
    await reopen(page, "Native failed request body");
    await page.getByRole("button", { name: "Toggle save failure", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    expect(await stored(page)).toBeNull();
});

test("native empty pending body survives Close and reload", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await editor(page).fill("");
    await close(page);
    await expect.poll(() => stored(page)).toBe("");
    await page.clock.resume();
    await page.reload();
    await edit(page, "");
});

test("native card switch flushes pending body into its own scope", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await editor(page).fill("Original card private draft");
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Original card private draft");
    await page.clock.resume();
    await edit(page, "Original description");
    expect(await stored(page, "a", "collaborative-card-draft-other-card")).toBeNull();
    await reopen(page, "Original card private draft");
});

test("native account switch preserves pending body only for its author", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await editor(page).fill("Pending account A private draft");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("actor")).toHaveText("collaborative-card-draft-user-b");
    await expect.poll(() => stored(page)).toBe("Pending account A private draft");
    await page.clock.resume();
    await edit(page, "Original description");
    expect(await stored(page, "b")).toBeNull();
    await editor(page).fill("Account B private draft");
    await close(page);
    await expect.poll(() => stored(page, "b")).toBe("Account B private draft");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await reopen(page, "Pending account A private draft");
    expect(await stored(page, "b")).toBe("Account B private draft");
});

test("native same-user session rotation preserves pending draft", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await editor(page).fill("Pending session private draft");
    await page.getByRole("button", { name: "Rotate session", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Pending session private draft");
    await page.clock.resume();
    await edit(page, "Pending session private draft");
    await expect(page.getByTestId("writes")).toHaveText("0");
});

test("native logout flushes pending draft and restores it only after same-author login", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await editor(page).fill("Pending logout private draft");
    await page.getByRole("button", { name: "Log out", exact: true }).click();
    await expect(page.getByText("Signed out", { exact: true })).toBeVisible();
    await expect(editor(page)).toHaveCount(0);
    await expect.poll(() => stored(page)).toBe("Pending logout private draft");
    await page.clock.resume();
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("actor")).toHaveText("collaborative-card-draft-user-a");
    await reopen(page, "Pending logout private draft");
});

test("native waiting editor retains restored draft when closed before initial sync", async ({ page }) => {
    const content = "Restored draft awaiting first sync";
    await page.addInitScript(({ key, content }) => localStorage.setItem(key, JSON.stringify({ content, savedAt: Date.now() })), {
        key: draftKey(),
        content,
    });
    await page.routeWebSocket(/127\.0\.0\.1:4243/, () => {});
    await page.goto(fixture);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(editor(page)).toHaveAttribute("contenteditable", "false");
    await expect(editor(page)).toHaveText(content);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByTestId("writes")).toHaveText("0");
    await close(page);
    expect(await stored(page)).toBe(content);
});

test("native delayed initial sync restores local seed over stale Yjs content", async ({ page }) => {
    await open(page);
    await editor(page).fill("Stale Yjs body");
    await close(page);
    await page.evaluate(({ key }) => localStorage.setItem(key, JSON.stringify({ content: "Newest local restored body", savedAt: Date.now() })), {
        key: draftKey(),
    });
    const held: Array<() => void> = [];
    let released = false;
    await page.routeWebSocket(/127\.0\.0\.1:4243/, (route) => {
        const server = route.connectToServer();
        server.onMessage((message) => {
            if (released) route.send(message);
            else held.push(() => route.send(message));
        });
    });
    await page.reload();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(editor(page)).toHaveAttribute("contenteditable", "false");
    await expect(editor(page)).toHaveText("Newest local restored body");
    await expect.poll(() => held.length).toBeGreaterThan(0);
    released = true;
    held.splice(0).forEach((send) => send());
    await expect(editor(page)).toHaveAttribute("contenteditable", "true");
    await expect(editor(page)).toHaveText("Newest local restored body");
    await expect.poll(() => stored(page)).toBe("Newest local restored body");
});

for (const width of [768, 390]) {
    test(`native pending draft survives Close/reopen at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await open(page);
        await pauseDebounce(page);
        await editor(page).fill(`Draft at ${width}px`);
        await close(page);
        await expect.poll(() => stored(page)).toBe(`Draft at ${width}px`);
        await page.clock.resume();
        await reopen(page, `Draft at ${width}px`);
    });
}

const imageReference = "http://127.0.0.1:4242/rich-draft/reference.png";
const fileReference = "http://127.0.0.1:4242/rich-draft/reference.txt";
async function insertReference(page: Page, kind: "Image" | "File", url: string, advanceClock = false) {
    await page.getByRole("button", { name: kind, exact: true }).click();
    await page.getByRole("menuitem", { name: "Insert via URL", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("textbox").fill(url);
    if (advanceClock) await page.clock.runFor(1);
    await dialog.getByRole("button", { name: "Accept", exact: true }).click();
    await expect(dialog).toHaveCount(0);
}
async function authorRichBody(page: Page) {
    await page.route(imageReference, (route) =>
        route.fulfill({
            contentType: "image/png",
            body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=", "base64"),
        })
    );
    await editor(page).fill("");
    await editor(page).press("ControlOrMeta+b");
    await page.keyboard.type("Bold draft");
    await editor(page).press("ControlOrMeta+b");
    await page.keyboard.type(" and ");
    await editor(page).press("ControlOrMeta+i");
    await page.keyboard.type("Italic draft");
    await editor(page).press("ControlOrMeta+i");
    await page.keyboard.type(" @");
    const mention = page.getByRole("combobox");
    await expect(mention).toBeVisible();
    await mention.fill("ali");
    await expect(page.getByRole("option")).toHaveCount(1);
    await expect(page.getByRole("option")).toContainText("Alice Kim");
    await page.getByRole("option").click();
    await expect(mention).toHaveCount(0);
    await page.keyboard.type(" reference body");
    await insertReference(page, "Image", imageReference);
}
async function expectRichBody(page: Page) {
    await expect(editor(page).locator("strong")).toContainText("Bold draft");
    await expect(editor(page).locator("em")).toContainText("Italic draft");
    await expect(editor(page).locator("[data-slate-value=alice]")).toContainText("Alice Kim");
    await expect(editor(page).locator(`img[src="${imageReference}"]`)).toBeVisible();
    await expect
        .poll(() =>
            editor(page)
                .locator(`img[src="${imageReference}"]`)
                .evaluate((image: HTMLImageElement) => image.naturalWidth)
        )
        .toBe(1);
    await expect(editor(page).getByRole("button", { name: "reference.txt", exact: true })).toBeVisible();
}

test("rich draft formatting, mention and media references survive pending Close, reload and save outcomes", async ({ page }, testInfo) => {
    await open(page, "?rich=public");
    await authorRichBody(page);
    await expect(editor(page).locator(`img[src="${imageReference}"]`)).toBeVisible();
    await pauseDebounce(page);
    await insertReference(page, "File", fileReference, true);
    await page.clock.runFor(50);
    await expectRichBody(page);
    expect(await stored(page)).not.toContain(fileReference);
    await close(page);
    const content = await stored(page);
    expect(content).toContain("**Bold draft**");
    expect(content).toContain("_Italic draft_");
    expect(content).toContain("[**@alice**](rich-draft-alice)");
    expect(content).toContain(imageReference);
    expect(content).toContain(fileReference);
    await testInfo.attach("serialized-rich-draft", { body: content!, contentType: "text/markdown" });
    await page.clock.resume();
    await page.reload();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(editor(page)).toHaveAttribute("contenteditable", "true");
    await expectRichBody(page);
    await page.screenshot({ path: testInfo.outputPath("restored-rich-body.png") });
    await page.getByRole("button", { name: "Toggle save failure", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    expect(await stored(page)).toBe(content);
    await expectRichBody(page);
    await page.getByRole("button", { name: "Toggle save failure", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    expect(await stored(page)).toBeNull();
    await expect(page.getByTestId("saved-body")).toHaveText(content!);
    await close(page);
    await page.getByRole("button", { name: "Reopen card", exact: true }).click();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(editor(page)).toHaveAttribute("contenteditable", "true");
    await expectRichBody(page);
});

test("rich draft PRIVATE card excludes mention choices despite legitimate project members", async ({ page }) => {
    await open(page, "?rich=private");
    await editor(page).fill("Private draft ");
    await editor(page).press("ControlOrMeta+End");
    await page.keyboard.type("@");
    const mention = page.getByRole("combobox");
    await expect(mention).toBeVisible();
    await mention.fill("ali");
    await expect(page.getByRole("option")).toHaveCount(0);
    await expect(page.getByText("No results", { exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(mention).toHaveCount(0);
    await expect(page.getByText("Card closed", { exact: true })).toBeVisible();
    expect(await stored(page)).not.toContain("rich-draft-alice");
});

test("rich draft references remain with their author and card at 390px", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, "?rich=public");
    await authorRichBody(page);
    await insertReference(page, "File", fileReference);
    await expectRichBody(page);
    await close(page);
    const content = await stored(page);
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await edit(page, "Original description");
    await expect(editor(page).locator("img, [data-slate-value]")).toHaveCount(0);
    await expect(editor(page).getByRole("button", { name: "reference.txt", exact: true })).toHaveCount(0);
    expect(await stored(page, "a", "collaborative-card-draft-other-card")).toBeNull();
    await close(page);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("actor")).toHaveText("collaborative-card-draft-user-b");
    await reopen(page, "Original description");
    await expect(editor(page).locator("img, [data-slate-value]")).toHaveCount(0);
    await expect(editor(page).getByRole("button", { name: "reference.txt", exact: true })).toHaveCount(0);
    expect(await stored(page, "b")).toBeNull();
    await close(page);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("actor")).toHaveText("collaborative-card-draft-user-a");
    await page.getByRole("button", { name: "Reopen card", exact: true }).click();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(editor(page)).toHaveAttribute("contenteditable", "true");
    await expectRichBody(page);
    expect(await stored(page)).toBe(content);
    await page.screenshot({ path: testInfo.outputPath("mobile-rich-author-restored.png") });
});
