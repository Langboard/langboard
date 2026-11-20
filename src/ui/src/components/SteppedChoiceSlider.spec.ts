import { expect, test } from "@playwright/test";

const path = "/src/components/SteppedChoiceSlider.fixture.html";

test("pointer movement previews with persistent fill and commits once on release", async ({ page }) => {
    await page.goto(path);
    const slider = page.getByRole("slider", { name: "Priority" });
    await expect(slider).toHaveCount(1);
    await expect(slider).toHaveAttribute("aria-valuetext", "Normal");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
    const track = (await page.locator(".stepped-choice-track").boundingBox())!;
    await page.mouse.move(track.x + track.width / 3, track.y + track.height / 2);
    await page.mouse.down();
    await page.mouse.move(track.x + (track.width * 2) / 3, track.y + track.height / 2);
    await expect(page.getByTestId("value")).toHaveText("P1");
    await expect(page.getByTestId("commits")).toHaveText("[]");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "emergency");
    const fill = (await page.locator(".stepped-choice-range").boundingBox())!;
    expect(fill.x).toBeCloseTo(track.x, 0);
    expect(fill.width / track.width).toBeCloseTo(2 / 3, 1);
    await page.mouse.up();
    await expect(page.getByTestId("commits")).toHaveText(JSON.stringify(["P1"]));
    await page.getByRole("button", { name: "External reset" }).click();
    await expect(slider).toHaveAttribute("aria-valuetext", "Normal");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
    await expect(page.getByTestId("commits")).toHaveText(JSON.stringify(["P1"]));
});

test("keyboard selection commits and warning effects are independent", async ({ page }) => {
    await page.goto(path);
    const slider = page.getByRole("slider");
    await slider.focus();
    await slider.press("Home");
    await expect(slider).toHaveAttribute("aria-valuetext", "Relaxed");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
    await slider.press("ArrowRight");
    await expect(slider).toHaveAttribute("aria-valuetext", "Normal");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
    await slider.press("ArrowRight");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "emergency");
    await slider.press("End");
    await expect(slider).toHaveAttribute("aria-valuetext", "Now!");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "critical");
    await expect(page.getByTestId("commits")).toHaveText(JSON.stringify(["P3", "P2", "P1", "P0"]));
    await expect(page.locator(".stepped-choice-range")).toHaveCSS("left", "0px");
});

test("an omitted effect stays static even at the highest choice", async ({ page }) => {
    await page.goto(path + "?no-effect");
    await page.getByRole("slider").focus();
    await page.getByRole("slider").press("End");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
    expect(await page.locator(".stepped-choice-range").evaluate((element) => getComputedStyle(element, "::after").animationName)).toBe("none");
});

test("disabled choices cannot preview or commit", async ({ page }) => {
    await page.goto(path + "?disabled");
    const slider = page.getByRole("slider");
    await expect(slider).toHaveAttribute("data-disabled", "");
    await page.locator(".stepped-choice-track").click({ position: { x: 1, y: 4 }, force: true });
    await slider.dispatchEvent("keydown", { key: "End" });
    await expect(page.getByTestId("value")).toHaveText("P2");
    await expect(page.getByTestId("commits")).toHaveText("[]");
});

for (const [lang, name, labels] of [
    ["en-US", "Priority", ["Relaxed", "Normal", "Urgent", "Now!"]],
    ["ko-KR", "우선도", ["여유", "보통", "급함", "지금!"]],
    ["ja-JP", "優先度", ["余裕", "通常", "急ぎ", "今すぐ！"]],
    ["zh-CN", "优先级", ["宽松", "普通", "紧急", "立即！"]],
] as const) {
    test(`localized labels and accessible value: ${lang}`, async ({ page }) => {
        await page.goto(`${path}?lang=${lang}`);
        const slider = page.getByRole("slider", { name });
        await expect(slider).toHaveAttribute("aria-valuetext", labels[1]);
        await expect(page.locator(".stepped-choice-labels span")).toHaveText([...labels]);
        await expect(page.locator(".stepped-choice-hint")).not.toBeEmpty();
        await slider.focus();
        await slider.press("End");
        await expect(slider).toHaveAttribute("aria-valuetext", labels[3]);
    });
}

test("reduced motion preserves warning information and fill without animation", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(path);
    const slider = page.getByRole("slider");
    await slider.focus();
    for (const key of ["ArrowRight", "End"]) {
        await slider.press(key);
        await expect(page.locator(".stepped-choice-range")).toBeVisible();
        await expect(page.locator(".stepped-choice-hint")).toBeVisible();
        expect(await page.locator(".stepped-choice-range").evaluate((element) => getComputedStyle(element, "::after").animationName)).toBe("none");
        expect(await page.locator(".stepped-choice-range").evaluate((element) => getComputedStyle(element, "::before").animationName)).toBe("none");
    }
    await expect(slider).toHaveAttribute("aria-valuetext", "Now!");
});

test("touch selection commits once at release on a narrow screen", async ({ browser, baseURL }) => {
    const context = await browser.newContext({ hasTouch: true, viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    try {
        await page.goto(new URL(path, baseURL).toString());
        const track = (await page.locator(".stepped-choice-track").boundingBox())!;
        const session = await context.newCDPSession(page);
        const start = { x: track.x + track.width / 3, y: track.y + track.height / 2 };
        const end = { x: track.x + track.width, y: start.y };
        await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [start] });
        await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [end] });
        await expect(page.getByTestId("value")).toHaveText("P0");
        await expect(page.getByTestId("commits")).toHaveText("[]");
        await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await expect(page.getByTestId("commits")).toHaveText(JSON.stringify(["P0"]));
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally {
        await context.close();
    }
});
