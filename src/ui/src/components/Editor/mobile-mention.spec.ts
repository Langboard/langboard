import { expect, test } from "@playwright/test";

test.use({ hasTouch: true });

for (const width of [320, 390, 720, 1440]) {
    test(`mention selection and editor-bound placement at ${width}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 844 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto("/src/components/Editor/mobile-mention.fixture.html");
        const editor = page.locator("[data-slate-editor]");
        await expect(editor).toContainText("Draft before mention");
        await editor.click();
        await page.keyboard.press("ControlOrMeta+End");
        await page.keyboard.type(" @");
        const input = page.getByRole("combobox");
        await page.screenshot({ path: testInfo.outputPath(`mention-${width}-trigger.png`) });
        await testInfo.attach("trigger-dom", {
            body: JSON.stringify(
                await page.evaluate(() => {
                    const input = document.querySelector("[role=combobox]") as HTMLElement;
                    const popup = document.querySelector("[data-plate-combobox-content]") as HTMLElement;
                    const editor = document.querySelector("[data-slate-editor]") as HTMLElement;
                    return {
                        input: input?.getBoundingClientRect().toJSON(),
                        popup: popup?.getBoundingClientRect().toJSON(),
                        editor: editor?.getBoundingClientRect().toJSON(),
                        focused: document.activeElement?.getAttribute("role"),
                    };
                })
            ),
            contentType: "application/json",
        });
        await expect(input).toBeVisible();
        const popup = page.locator("[data-plate-combobox-content]");
        await expect(popup).toBeVisible();
        await page.screenshot({ path: testInfo.outputPath(`mention-${width}-open.png`) });
        const editorBox = (await (width < 768 ? page.getByTestId("editor-boundary") : editor).boundingBox())!;
        const popupBox = (await popup.boundingBox())!;
        if (width < 768) {
            expect(Math.abs(popupBox.width - editorBox.width)).toBeLessThanOrEqual(2);
            expect(Math.abs(popupBox.x - editorBox.x)).toBeLessThanOrEqual(2);
            expect(popupBox.y + popupBox.height).toBeLessThanOrEqual(editorBox.y + 1);
            expect(editorBox.y - popupBox.y - popupBox.height).toBeLessThanOrEqual(8);
        } else {
            expect(popupBox.width).toBe(300);
            expect(popupBox.width).toBeLessThan(editorBox.width);
        }
        await input.tap();
        await input.fill("ali");
        await expect(page.getByRole("option")).toHaveCount(1);
        await expect(page.getByRole("option")).toContainText("Alice Kim");
        await page.getByRole("option").tap();
        await expect(input).toHaveCount(0);
        await page.getByRole("button", { name: "Inspect document" }).click();
        await expect(page.getByTestId("nodes")).toContainText(/"key":"mention-alice"/);
        await expect(page.getByTestId("serialized")).toContainText("mention-alice");
        await expect(page.getByTestId("serialized")).toContainText("Draft before mention");
        await page.screenshot({ path: testInfo.outputPath(`mention-${width}-selected.png`) });
        expect(errors).toEqual([]);
    });
}

test("mobile viewport resize, composition filtering, and keyboard selection retain mention identity", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/src/components/Editor/mobile-mention.fixture.html");
    const editor = page.locator("[data-slate-editor]");
    await editor.click();
    await page.keyboard.press("ControlOrMeta+End");
    await page.keyboard.type(" @");
    const input = page.getByRole("combobox");
    const popup = page.locator("[data-plate-combobox-content]");
    await expect(input).toBeVisible();
    await page.setViewportSize({ width: 390, height: 420 });
    await expect
        .poll(async () => {
            const bounds = (await popup.boundingBox())!;
            const anchor = (await page.getByTestId("editor-boundary").boundingBox())!;
            return Math.abs(bounds.y + bounds.height + 4 - anchor.y);
        })
        .toBeLessThanOrEqual(2);
    expect((await popup.boundingBox())!.y).toBeGreaterThanOrEqual(0);
    await page.screenshot({ path: testInfo.outputPath("mention-390-reduced-viewport.png") });
    await input.dispatchEvent("compositionstart", { data: "" });
    await input.fill("민");
    await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, keyCode: 229 });
    await expect(input).toBeVisible();
    await input.dispatchEvent("compositionend", { data: "민" });
    await expect(page.getByRole("option")).toHaveCount(1);
    await expect(page.getByRole("option")).toContainText("민지");
    await page.getByRole("option").tap();
    await expect(page.getByTestId("serialized")).toContainText("mention-korean");
    await editor.click();
    await page.keyboard.press("ControlOrMeta+End");
    await page.keyboard.type(" @");
    await input.fill("bo");
    await expect(page.getByRole("option")).toHaveCount(1);
    await expect(input).toBeFocused();
    await page.evaluate(() => {
        const log: unknown[] = [];
        (window as unknown as { mentionEvents: unknown[] }).mentionEvents = log;
        for (const type of ["focusout", "focusin", "keydown", "click"])
            document.addEventListener(
                type,
                (event) => {
                    const target = event.target as HTMLElement;
                    const related = (event as FocusEvent).relatedTarget as HTMLElement | null;
                    log.push({ type, role: target.getAttribute("role"), related: related?.getAttribute?.("role") });
                },
                true
            );
    });
    await page.keyboard.press("Enter");
    await testInfo.attach("keyboard-events", {
        body: JSON.stringify(await page.evaluate(() => (window as unknown as { mentionEvents: unknown[] }).mentionEvents)),
        contentType: "application/json",
    });
    await expect(page.getByTestId("serialized")).toContainText("mention-bob");
    await expect(page.getByTestId("serialized")).toContainText("mention-korean");
    await expect(page.getByTestId("serialized")).toContainText("Draft before mention");
    await editor.click();
    await page.keyboard.press("ControlOrMeta+End");
    await page.keyboard.type(" @");
    await input.fill("no-match");
    await expect(page.getByRole("option")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(input).toHaveCount(0);
    await expect(page.getByTestId("serialized")).toContainText("@no-match");
    await expect(page.getByTestId("serialized")).toContainText("mention-bob");
});
