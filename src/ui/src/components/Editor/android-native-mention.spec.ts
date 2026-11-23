import { expect, test, type CDPSession, type Page } from "@playwright/test";

test.use({
    userAgent: "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
    viewport: { width: 390, height: 844 },
    hasTouch: true,
});

async function openEditor(page: Page, content = "") {
    await page.goto(`/src/components/Editor/mobile-mention.fixture.html?content=${encodeURIComponent(content)}`);
    const editor = page.locator("[data-slate-editor]");
    await editor.click();
    if (content) {
        await page.keyboard.press("ControlOrMeta+End");
        // Let Slate complete Android's selection reconciliation before the edit.
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0))));
    }
    return page.context().newCDPSession(page);
}

async function compose(session: CDPSession, text: string) {
    await session.send("Input.imeSetComposition", { text, selectionStart: text.length, selectionEnd: text.length });
}

test("Android native text insertion opens the existing mention input", async ({ page }, testInfo) => {
    const session = await openEditor(page);
    await page.evaluate(() => {
        const events: object[] = [];
        Object.assign(window, { nativeMentionEvents: events });
        document.addEventListener(
            "beforeinput",
            (event) => {
                const input = event as InputEvent;
                events.push({
                    data: input.data,
                    inputType: input.inputType,
                    isComposing: input.isComposing,
                    cancelable: input.cancelable,
                    trusted: input.isTrusted,
                });
            },
            true
        );
    });
    // CDP performs a real browser text edit with trusted native beforeinput;
    // Android UA activates Slate's Android input manager. No keyboard.type().
    await session.send("Input.insertText", { text: "@" });
    await expect(page.getByRole("combobox")).toBeVisible();
    const events = await page.evaluate(() => (window as unknown as { nativeMentionEvents: object[] }).nativeMentionEvents);
    expect(events).toContainEqual({ data: "@", inputType: "insertText", isComposing: false, cancelable: true, trusted: true });
    await testInfo.attach("native-trigger-events", { body: JSON.stringify(events), contentType: "application/json" });
    await page.getByRole("combobox").fill("ali");
    await expect(page.getByRole("option")).toHaveCount(1);
    await page.getByRole("option").tap();
    await expect(page.getByTestId("serialized")).toContainText("mention-alice");
    await expect(page.getByRole("combobox")).toHaveCount(0);
});

test("Android native trigger retains an existing draft and invalid previous characters stay literal", async ({ page }) => {
    const session = await openEditor(page, 'Draft before mention "');
    await session.send("Input.insertText", { text: "@" });
    await expect(page.getByRole("combobox")).toBeVisible();
    await page.getByRole("combobox").fill("no-match");
    await expect(page.getByRole("option")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("serialized")).toContainText('Draft before mention "@no-match');
    await session.send("Input.insertText", { text: "@" });
    await expect(page.getByRole("combobox")).toHaveCount(0);
    await expect(page.getByTestId("serialized")).toContainText("@no-match@");
});

test("Android native Korean composition retains draft and cannot select a mention before commit", async ({ page }) => {
    const session = await openEditor(page);
    await compose(session, "ㅎ");
    await compose(session, "하");
    await compose(session, "한");
    await expect(page.getByRole("combobox")).toHaveCount(0);
    await session.send("Input.insertText", { text: "한" });
    await expect(page.getByTestId("serialized")).toContainText("한");
    await session.send("Input.insertText", { text: " " });
    await session.send("Input.insertText", { text: "@" });
    const input = page.getByRole("combobox");
    await expect(input).toBeVisible();
    await compose(session, "ㅁ");
    await compose(session, "미");
    await compose(session, "민");
    await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, keyCode: 229 });
    await expect(input).toBeVisible();
    await expect(page.getByTestId("serialized")).not.toContainText("mention-korean");
    await session.send("Input.insertText", { text: "민" });
    await expect(page.getByRole("option")).toHaveCount(1);
    await page.getByRole("option").tap();
    await expect(page.getByTestId("serialized")).toContainText("한");
    await expect(page.getByTestId("serialized")).toContainText("mention-korean");
});

test("Android native slash trigger uses the existing shared combobox path", async ({ page }) => {
    const session = await openEditor(page);
    await session.send("Input.insertText", { text: "/" });
    await expect(page.getByRole("combobox")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("serialized")).toContainText("/");
});

test("Android native internal-link trigger uses its existing plugin", async ({ page }) => {
    const session = await openEditor(page);
    await session.send("Input.insertText", { text: "{{" });
    await expect(page.getByRole("combobox")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("combobox")).toHaveCount(0);
});
