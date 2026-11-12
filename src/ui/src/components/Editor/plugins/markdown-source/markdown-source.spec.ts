import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/src/components/Editor/plugins/markdown-source/markdown-source.fixture.html");
});

test("description defaults on with source left; swap and mode persist per user, comments stay rich", async ({ page }) => {
    const raw = page.getByRole("textbox", { name: "Markdown source", exact: true });
    const preview = page.getByLabel("Markdown preview", { exact: true });
    await expect(raw).toBeVisible();
    expect((await raw.boundingBox())!.x).toBeLessThan((await preview.boundingBox())!.x);
    await page.getByRole("button", { name: "Swap panes" }).click();
    await expect.poll(async () => (await raw.boundingBox())!.x > (await preview.boundingBox())!.x).toBe(true);
    await page.reload();
    await expect.poll(async () => (await raw.boundingBox())!.x > (await preview.boundingBox())!.x).toBe(true);
    await page.getByRole("button", { name: "Markdown split", exact: true }).click();
    await expect(raw).toHaveCount(0);
    await page.reload();
    await expect(raw).toHaveCount(0);
    await page.getByLabel("User", { exact: true }).selectOption("user-b");
    await expect(raw).toBeVisible();
    await page.getByLabel("Comment editor", { exact: true }).check();
    await expect(raw).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Markdown split", exact: true })).toHaveCount(0);
    await page.getByLabel("Comment editor", { exact: true }).uncheck();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(raw).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Rich content", exact: true })).toBeEditable();
});

test("raw formatting survives save and mode changes; shortcuts and source undo work", async ({ page }) => {
    const raw = page.getByRole("textbox", { name: "Markdown source", exact: true });
    const text = "## 안녕\n\n9. item\n\nLiteral **bold**  \n";
    await raw.fill(text);
    await page.getByRole("button", { name: "Save draft", exact: true }).click();
    expect(await page.locator("[data-saved-draft]").textContent()).toBe(text);
    await page.getByRole("button", { name: "Markdown split", exact: true }).click();
    await page.getByRole("button", { name: "Save draft", exact: true }).click();
    expect(await page.locator("[data-saved-draft]").textContent()).toBe(text);
    await page.getByRole("button", { name: "Markdown split", exact: true }).click();
    await raw.fill("- [x] task");
    await raw.press("End");
    await raw.press("Enter");
    await expect(raw).toHaveValue("- [x] task\n- [ ] ");
    await raw.press("Control+z");
    await expect(raw).toHaveValue("- [x] task");
});

test("source undo and redo survive collaborative identity normalization but never undo a different document", async ({ page }) => {
    const raw = page.getByRole("textbox", { name: "Markdown source", exact: true });
    await raw.fill("안녕 세계");
    await raw.evaluate((element) => (element as HTMLTextAreaElement).setSelectionRange(2, 3));
    await raw.press("Control+b");
    await expect(raw).toHaveValue("안녕** **세계");
    await page.getByRole("button", { name: "Normalize document identity", exact: true }).click();
    await raw.focus();
    await raw.press("Control+z");
    await expect(raw).toHaveValue("안녕 세계");
    await raw.press("Control+Shift+z");
    await expect(raw).toHaveValue("안녕** **세계");
    await page.getByRole("button", { name: "Remote edit", exact: true }).click();
    await expect(raw).toHaveValue(/^Remote /);
    const peer = await raw.inputValue();
    await raw.focus();
    await raw.press("Control+z");
    await expect(raw).toHaveValue(peer);
});

test("source and preview scroll/caret anchors follow each other", async ({ page }) => {
    const raw = page.getByRole("textbox", { name: "Markdown source", exact: true });
    const preview = page.getByLabel("Markdown preview", { exact: true });
    await raw.evaluate((element) => {
        const input = element as HTMLTextAreaElement;
        input.scrollTop = input.scrollHeight / 2;
        input.dispatchEvent(new Event("scroll"));
    });
    await expect.poll(() => preview.evaluate((element) => element.scrollTop)).toBeGreaterThan(50);
    await page.waitForTimeout(160);
    await preview.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const header = document.createElement("div");
        header.dataset.scrollOccluder = "true";
        Object.assign(header.style, {
            position: "fixed",
            left: `${rect.left}px`,
            top: `${rect.top}px`,
            width: `${rect.width}px`,
            height: "40px",
            zIndex: "2147483647",
        });
        document.body.appendChild(header);
        element.scrollTop = 0;
        element.dispatchEvent(new Event("scroll"));
    });
    await expect.poll(() => raw.evaluate((element) => (element as HTMLTextAreaElement).scrollTop)).toBeLessThan(50);
    await page.waitForTimeout(160);
    await preview.evaluate((element) => {
        element.scrollTop = (element.scrollHeight - element.clientHeight) / 2;
        element.dispatchEvent(new Event("scroll"));
    });
    await expect.poll(() => raw.evaluate((element) => (element as HTMLTextAreaElement).scrollTop)).toBeGreaterThan(50);
    await page.locator("[data-scroll-occluder]").evaluate((element) => element.remove());
    await preview.getByText("Original", { exact: true }).click();
    await expect(raw).toBeFocused();
    const offset = await raw.evaluate((element) => (element as HTMLTextAreaElement).selectionStart);
    expect(offset).toBeGreaterThanOrEqual(3);
    expect(offset).toBeLessThanOrEqual(11);
});

test("remote edits refresh source; reduced motion skips pane animations", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.getByRole("button", { name: "Remote edit", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Markdown source", exact: true })).toHaveValue(/Remote/);
    await page.getByRole("button", { name: "Swap panes" }).click();
    expect(await page.locator("[data-markdown-source]").evaluate((element) => element.getAnimations({ subtree: true }).length)).toBe(0);
});
