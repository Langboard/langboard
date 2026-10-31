import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { parseCardPresentation, cardPresentationText, cardVisibilityPresentation } from "./CardPresentation";
const load = (locale: string) => JSON.parse(readFileSync(new URL(`../../assets/locales/${locale}/card.json`, import.meta.url), "utf8"));
const [en, ko, ja, zh] = ["en-US", "ko-KR", "ja-JP", "zh-CN"].map(load);
for (const [locale, texts] of Object.entries({ "en-US": en, "ko-KR": ko, "ja-JP": ja, "zh-CN": zh })) {
    test(`${locale} visibility badge shows localized hover and keyboard description`, async ({ page }) => {
        await page.goto("/src/core/cards/CardPresentation.fixture.html");
        await page.getByRole("combobox", { name: "Language" }).selectOption(locale);
        const badge = page.locator("[data-card-presentation=visibility\\.whisper]").first();
        await expect(badge).toContainText(texts.Whisper);
        await badge.hover();
        await expect(page.getByRole("tooltip")).toHaveText(texts["Whisper visibility guidance"]);
        await page.mouse.move(0, 0);
        await badge.focus();
        await expect(page.getByRole("tooltip")).toHaveText(texts["Whisper visibility guidance"]);
        await page.keyboard.press("Escape");
        await expect(page.getByRole("tooltip")).toHaveCount(0);
        await page.setViewportSize({ width: 390, height: 844 });
        await badge.focus();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("extension kinds use locale fallback and cannot override native privacy or policy", () => {
    const item = {
        version: 1,
        key: "app.glitchtip.issue",
        axis: "origin",
        name: "GlitchTip issue",
        description: "App origin.",
        translations: { ko: { name: "오류 카드", description: "앱에서 만든 카드입니다." } },
    };
    const parsed = parseCardPresentation(JSON.stringify(item))!;
    expect(cardPresentationText(parsed, "ko-KR").name).toBe("오류 카드");
    expect(cardPresentationText(parsed, "fr-FR").name).toBe("GlitchTip issue");
    for (const change of [
        { axis: "visibility" },
        { key: "visibility.private" },
        { visibility: "SHARED" },
        { completed: true },
        { translations: [] },
        { icon: "x".repeat(33) },
    ]) {
        expect(parseCardPresentation(JSON.stringify({ ...item, ...change }))).toBeUndefined();
    }
    expect(parseCardPresentation("invalid")).toBeUndefined();
    expect(cardVisibilityPresentation("PRIVATE", true)).toBe("private");
    expect(cardVisibilityPresentation("INTERNAL", true)).toBe("whisper");
    expect(cardVisibilityPresentation("INTERNAL", false)).toBeUndefined();
    expect(cardVisibilityPresentation("SHARED", true)).toBeUndefined();
});

test("SDK metadata updates the common badge without replacing visibility", async ({ page }) => {
    await page.goto("/src/core/cards/CardPresentation.fixture.html");
    await page.getByRole("combobox", { name: "Language" }).selectOption("ko-KR");
    await page.getByRole("button", { name: "Attach app type" }).click();
    const appBadge = page.locator("[data-card-presentation-axis=origin]");
    await expect(appBadge).toHaveText("🔗깃허브 이슈");
    await expect(page.locator("[data-card-presentation-axis=visibility]").first()).toContainText("귓속말");
    await appBadge.hover();
    await expect(page.getByRole("tooltip")).toHaveText("앱에서 제공한 출처입니다.");
    await page.getByRole("button", { name: "Remove app type" }).click();
    await expect(appBadge).toHaveCount(0);
    await expect(page.locator("[data-card-presentation-axis=visibility]").first()).toContainText("귓속말");
});

for (const width of [1920, 390]) {
    test(`native visibility follows project member transitions without changing card values at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/core/cards/CardPresentation.fixture.html");
        await page.getByRole("combobox", { name: "Language" }).selectOption("ko-KR");
        const internal = page.getByTestId("internal-card");
        const privateCard = page.getByTestId("private-card");
        await expect(internal.locator("[data-card-presentation-axis=visibility]")).toHaveCount(0);
        await expect(privateCard).toContainText("프라이빗");
        await page.getByRole("button", { name: "Add external member" }).click();
        await expect(internal).toContainText("귓속말");
        await expect(privateCard).toContainText("프라이빗");
        await expect(page.getByTestId("stored-visibility")).toHaveText("INTERNAL");
        await internal.locator("[data-card-presentation-axis=visibility]").hover();
        await expect(page.getByRole("tooltip")).toHaveText(ko["Whisper visibility guidance"]);
        await page.getByRole("button", { name: "Remove external member" }).click();
        await expect(internal.locator("[data-card-presentation-axis=visibility]")).toHaveCount(0);
        await expect(privateCard).toContainText("프라이빗");
        await expect(page.getByTestId("stored-visibility")).toHaveText("INTERNAL");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("membership classification changes refresh native visibility without replacing project members", async ({ page }) => {
    await page.goto("/src/core/cards/CardPresentation.fixture.html");
    await page.getByRole("combobox", { name: "Language" }).selectOption("ko-KR");
    const internal = page.getByTestId("internal-card");
    await page.getByRole("button", { name: "Add external member" }).click();
    await expect(internal).toContainText("귓속말");
    await page.getByRole("button", { name: "Classify member as internal", exact: true }).click();
    await expect(internal.locator("[data-card-presentation-axis=visibility]")).toHaveCount(0);
    await page.getByRole("button", { name: "Classify member as external", exact: true }).click();
    await expect(internal).toContainText("귓속말");
    await expect(page.getByTestId("private-card")).toContainText("프라이빗");
    await expect(page.getByTestId("stored-visibility")).toHaveText("INTERNAL");
    await page.getByRole("button", { name: "Classify member as internal", exact: true }).click();
    await expect(internal.locator("[data-card-presentation-axis=visibility]")).toHaveCount(0);
    await page.getByRole("button", { name: "Toggle member view" }).click();
    await expect(internal).toHaveCount(0);
    await page.getByRole("button", { name: "Update hidden member classification" }).click();
    await page.getByRole("button", { name: "Toggle member view" }).click();
    await expect(internal).toContainText("귓속말");
    await page.getByRole("button", { name: "Classify member as internal", exact: true }).click();
    await expect(internal.locator("[data-card-presentation-axis=visibility]")).toHaveCount(0);
});

for (const width of [320, 390]) {
    test(`native assignee and checklist worker roles remain independent at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/core/cards/CardPresentation.fixture.html");
        await page.getByRole("combobox", { name: "Language", exact: true }).selectOption("en-US");
        const scenario = page.getByRole("combobox", { name: "Role scenario" });
        const board = page.getByTestId("native-role-board");
        const detail = page.getByTestId("native-role-detail");
        const assignees = board.getByRole("group", { name: "Assignees", exact: true });
        const workers = board.getByRole("group", { name: "Checklist workers", exact: true });
        await expect(assignees.getByText("AO", { exact: true })).toBeVisible();
        await expect(workers).toHaveCount(0);
        await scenario.selectOption("worker");
        await expect(assignees.getByRole("img", { name: "Unassigned" })).toBeVisible();
        await expect(assignees.getByText("CW", { exact: true })).toHaveCount(0);
        await expect(workers.getByText("CW", { exact: true })).toBeVisible();
        await expect(detail.getByRole("group", { name: "Checklist workers", exact: true })).toBeVisible();
        await scenario.selectOption("both");
        await expect(assignees.getByText("AO", { exact: true })).toBeVisible();
        await expect(workers.getByText("CW", { exact: true })).toBeVisible();
        await expect(assignees.locator('[tabindex="0"][aria-label^="Working now:"]')).toHaveCount(0);
        const timer = workers.locator('[tabindex="0"][aria-label^="Working now:"]');
        await timer.focus();
        await page.keyboard.press("Tab");
        await page.keyboard.press("Shift+Tab");
        await expect(timer).toBeFocused();
        const runningTitle = await timer.getAttribute("aria-label");
        await expect.poll(() => timer.getAttribute("aria-label")).not.toBe(runningTitle);
        await timer.hover();
        await expect(timer).toHaveAttribute("title", /Inspect checklist/);
        await scenario.selectOption("paused");
        const paused = workers.locator('[tabindex="0"][aria-label^="Work paused:"]');
        await paused.focus();
        const pausedTitle = await paused.getAttribute("aria-label");
        await page.waitForTimeout(1200);
        await expect(paused).toHaveAttribute("aria-label", pausedTitle!);
        await scenario.selectOption("same");
        await expect(assignees.getByText("AO", { exact: true })).toBeVisible();
        await expect(workers.getByText("AO", { exact: true })).toBeVisible();
        await expect(detail.locator('[tabindex="0"][aria-label^="Working now:"]')).toHaveCount(1);
        await scenario.selectOption("assignee");
        await expect(workers).toHaveCount(0);
        await expect(detail.getByRole("group", { name: "Checklist workers", exact: true })).toHaveCount(0);
        await expect(assignees.getByText("AO", { exact: true })).toBeVisible();
        await scenario.selectOption("outsider");
        await expect(assignees.getByRole("img", { name: "Unassigned" })).toBeVisible();
        await expect(workers).toHaveCount(0);
        await expect(board.getByText("OS", { exact: true })).toHaveCount(0);
        await expect(detail.getByText("OS", { exact: true })).toHaveCount(0);
        await scenario.selectOption("private");
        await expect(assignees).toHaveCount(0);
        await expect(workers).toHaveCount(0);
        await expect(board.getByRole("img", { name: "Unassigned" })).toHaveCount(0);
        await expect(detail.getByRole("group", { name: "Checklist workers", exact: true })).toHaveCount(0);
        await expect(detail.getByTitle("Assign members", { exact: true })).toHaveCount(0);
        await scenario.selectOption("both");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: `/private/tmp/langboard-native-roles-${width}.png`, fullPage: true });
    });
}

for (const [locale, texts] of Object.entries({ "en-US": en, "ko-KR": ko, "ja-JP": ja, "zh-CN": zh })) {
    test(`${locale} native role labels are localized`, async ({ page }) => {
        await page.goto("/src/core/cards/CardPresentation.fixture.html");
        await page.getByRole("combobox", { name: "Language", exact: true }).selectOption(locale);
        await page.getByRole("combobox", { name: "Role scenario" }).selectOption("worker");
        const board = page.getByTestId("native-role-board");
        await expect(board.getByRole("group", { name: texts.Assignees, exact: true })).toBeVisible();
        await expect(board.getByRole("img", { name: texts.Unassigned })).toBeVisible();
        await expect(board.getByRole("group", { name: texts["Checklist workers"], exact: true })).toContainText(texts["Checklist workers"]);
        await expect(page.getByTestId("native-role-detail").getByRole("group", { name: texts["Checklist workers"], exact: true })).toBeVisible();
    });
}

test("native detail assignment mutation contains selected assignees only", async ({ page }) => {
    await page.goto("/src/core/cards/CardPresentation.fixture.html");
    await page.getByRole("combobox", { name: "Language", exact: true }).selectOption("en-US");
    await page.getByRole("combobox", { name: "Role scenario" }).selectOption("worker");
    const detail = page.getByTestId("native-role-detail");
    await expect(detail.getByRole("button", { name: "Assign members", exact: true })).toHaveCount(0);
    await detail.getByRole("button", { name: "Edit assignments" }).click();
    await detail.getByRole("button", { name: "Assign members", exact: true }).click();
    const editor = page.locator('[contenteditable="true"]').last();
    await editor.fill("Assigned");
    await page.getByRole("option", { name: "Assigned Owner", exact: true }).click();
    await expect(page.getByTestId("assignment-request")).toHaveText('{"assigned_users":["role-owner"]}');
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    await expect(detail.getByRole("button", { name: "Assign members", exact: true })).toBeFocused();
    await expect(editor).toHaveCount(0);
    await expect(
        page.getByTestId("native-role-board").getByRole("group", { name: "Assignees", exact: true }).getByText("AO", { exact: true })
    ).toBeVisible();
    await expect(detail.getByRole("group", { name: "Checklist workers", exact: true }).getByText("CW", { exact: true })).toBeVisible();
    await expect(detail.locator('[tabindex="0"][aria-label^="Working now:"]')).toHaveCount(1);
    await expect(
        detail.getByText("AO", { exact: true }).locator('xpath=ancestor::*[@tabindex="0" and starts-with(@aria-label, "Working now:")]')
    ).toHaveCount(0);
});

for (const width of [320, 390])
    test(`Escape dismisses assignment suggestions before the editor and restores focus at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/src/core/cards/CardPresentation.fixture.html");
        await page.getByRole("combobox", { name: "Language", exact: true }).selectOption("en-US");
        const detail = page.getByTestId("native-role-detail");
        await detail.getByRole("button", { name: "Edit assignments" }).click();
        const trigger = detail.getByRole("button", { name: "Assign members", exact: true });
        await trigger.click();
        const editor = page.locator('[contenteditable="true"]').last();
        await editor.fill("Checklist");
        await expect(page.getByRole("option", { name: "Checklist Worker", exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(page.getByRole("listbox", { name: "Suggestions" })).toHaveCount(0);
        await expect(editor).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(editor).toHaveCount(0);
        await expect(trigger).toBeFocused();
        await expect(page.getByTestId("assignment-request")).toBeEmpty();
    });
