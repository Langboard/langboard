import { expect, test } from "@playwright/test";

test("disabled retrieval settings reject native submit events", async ({ page }) => {
    await page.goto("/src/pages/SettingsPage/components/internalBots/retrieval-settings.fixture.html?readonly");
    await expect(page.locator("form button[type=submit]")).toBeDisabled();
    await page.locator("form").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await expect(page.locator("output")).toHaveText("{}");
});

for (const width of [1440, 390]) {
    test(`retrieval modes preserve supported settings at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        await page.goto("/src/pages/SettingsPage/components/internalBots/retrieval-settings.fixture.html");
        const form = page.locator("form");
        await expect(form.locator('[name="score_threshold"]')).toBeVisible();
        await expect(form.locator('[name="search_type"]')).toHaveCount(0);
        await form.locator('[name="store"]').selectOption("qdrant");
        await form.locator('[name="external_url"]').fill("https://vectors.example.invalid");
        await form.locator('[name="collection_prefix"]').fill("instance_documents");
        await form.locator('[name="search_type"]').selectOption("mmr");
        await expect(form.locator('[name="score_threshold"]')).toHaveCount(0);
        await form.locator('[name="fetch_k"]').fill("3");
        await form.locator('[name="lambda_mult"]').fill("0.7");
        await form.locator('button[type="submit"]').click();
        await expect(page.locator("output")).toHaveText("{}");
        expect(await form.locator('[name="fetch_k"]').evaluate((node: HTMLInputElement) => node.validity.customError)).toBe(true);
        await form.locator('[name="fetch_k"]').fill("12");
        await form.locator('button[type="submit"]').click();
        await expect(page.locator("output")).toContainText('"search_type":"mmr"');
        const saved = JSON.parse((await page.locator("output").textContent())!);
        expect(saved).toMatchObject({ store: "qdrant", collection_prefix: "instance_documents", search_type: "mmr", fetch_k: 12, lambda_mult: 0.7 });
        expect(saved.score_threshold).toBeUndefined();
        await form.locator('[name="search_type"]').selectOption("similarity");
        await form.locator('[name="score_threshold"]').fill("0.6");
        await form.locator('button[type="submit"]').click();
        await expect(page.locator("output")).toContainText('"score_threshold":0.6');
        await form.locator('[name="score_threshold"]').fill("");
        await form.locator('button[type="submit"]').click();
        await expect(page.locator("output")).toContainText('"score_threshold":null');
        await form.locator('[name="store"]').selectOption("sqlite");
        await form.locator('button[type="submit"]').click();
        const local = JSON.parse((await page.locator("output").textContent())!);
        expect(local.store).toBe("sqlite");
        expect(local.search_type).toBe("similarity");
        expect(local.external_url).toBeUndefined();
        expect(local.collection_prefix).toBeUndefined();
        await form.locator('[name="store"]').selectOption("qdrant");
        await expect(form.locator('[name="search_type"]')).toHaveValue("similarity");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: `test-results/retrieval-${width}.png`, fullPage: true });
    });
}

for (const [locale, captions] of Object.entries({
    "en-US": ["Recursive", "Tokens", "Start of chunk"],
    "ko-KR": ["재귀 분할", "토큰 수", "조각 시작"],
    "ja-JP": ["再帰分割", "トークン数", "チャンクの先頭"],
    "zh-CN": ["递归拆分", "令牌数", "片段开头"],
})) {
    test(`splitter options use localized captions and stable values for ${locale}`, async ({ page }) => {
        await page.addInitScript((language) => localStorage.setItem("lang", language), locale);
        await page.goto("/src/pages/SettingsPage/components/internalBots/retrieval-settings.fixture.html");
        const form = page.locator("form");
        await expect(form.locator('[name="splitter.type"] option[value="recursive"]')).toHaveText(captions[0]);
        await expect(form.locator('[name="splitter.length_unit"] option[value="tokens"]')).toHaveText(captions[1]);
        await expect(form.locator('[name="splitter.keep_separator"] option[value="start"]')).toHaveText(captions[2]);
        await form.locator('[name="splitter.length_unit"]').selectOption("tokens");
        await expect(form.locator('[name="splitter.encoding"]')).toHaveValue("cl100k_base");
        await form.locator('button[type="submit"]').click();
        const saved = JSON.parse((await page.locator("output").textContent())!);
        expect(saved.splitter).toMatchObject({ type: "recursive", length_unit: "tokens", keep_separator: "start", encoding: "cl100k_base" });
        if (locale === "ko-KR") {
            await page.setViewportSize({ width: 390, height: 1000 });
            await page.screenshot({ path: ".verification/retrieval-options-ko-mobile-20261018.png", fullPage: true });
        }
    });
}
