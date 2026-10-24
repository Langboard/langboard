import { expect, test } from "@playwright/test";
test("server language applies after every login and direct account switch despite legacy marker", async ({ page }) => {
    await page.addInitScript(() => {
        localStorage.setItem("lang", "zh-CN");
        localStorage.setItem("has-set-lang-langboard", "true");
    });
    await page.goto("/src/core/providers/AuthLanguage.fixture.html");
    await page.getByRole("button", { name: "Login A", exact: true }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-a", language: "ko-KR" }));
    await page.getByRole("button", { name: "Local Chinese" }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ language: "zh-CN" }).slice(1, -1));
    await page.getByRole("button", { name: "Refresh A" }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ language: "zh-CN" }).slice(1, -1));
    await page.getByRole("button", { name: "Logout" }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ user: null }).slice(1, -1));
    await page.getByRole("button", { name: "Login A", exact: true }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ language: "ko-KR" }).slice(1, -1));
    await page.getByRole("button", { name: "Login B", exact: true }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-b", language: "ja-JP" }));
    expect(await page.evaluate(() => document.documentElement.lang)).toBe("ja-JP");
});
test("unset preference preserves detected cache and invalid preference falls back to English", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("lang", "zh-CN"));
    await page.goto("/src/core/providers/AuthLanguage.fixture.html");
    await page.getByRole("button", { name: "Login unset" }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-unset", language: "zh-CN" }));
    await page.getByRole("button", { name: "Login invalid" }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-invalid", language: "en-US" }));
});

for (const [browserLocale, expected] of [
    ["ja-JP", "ja-JP"],
    ["ko-KR", "ko-KR"],
    ["fr-FR", "en-US"],
]) {
    test.describe(`unset preference with browser locale ${browserLocale}`, () => {
        test.use({ locale: browserLocale });

        test("uses navigator without a cache and retains it after login and reload", async ({ page }) => {
            await page.goto("/src/core/providers/AuthLanguage.fixture.html");
            await expect(page.locator("output")).toContainText(`"language":"${expected}"`);
            await page.getByRole("button", { name: "Login unset", exact: true }).click();
            await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-unset", language: expected }));
            expect(await page.evaluate(() => localStorage.getItem("lang"))).toBe(expected);
            expect(await page.evaluate(() => document.documentElement.lang)).toBe(expected);
            await page.reload();
            await expect(page.locator("output")).toContainText(`"language":"${expected}"`);
        });

        test("uses the cache before navigator for an unset account", async ({ page }) => {
            await page.addInitScript(() => localStorage.setItem("lang", "zh-CN"));
            await page.goto("/src/core/providers/AuthLanguage.fixture.html");
            await page.getByRole("button", { name: "Login unset", exact: true }).click();
            await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-unset", language: "zh-CN" }));
        });
    });
}

test("resolved instance and English fallback apply across accounts without persisting a personal choice", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("lang", "zh-CN"));
    let writes = 0;
    page.on("request", (request) => {
        if (request.method() === "PUT") writes++;
    });
    await page.goto("/src/core/providers/AuthLanguage.fixture.html");
    for (const [button, uid, language] of [
        ["Login inherited", "user-inherited", "ko-KR"],
        ["Login B", "user-b", "ja-JP"],
        ["Login fallback", "user-fallback", "en-US"],
        ["Login inherited", "user-inherited", "ko-KR"],
    ]) {
        await page.getByRole("button", { name: button, exact: true }).click();
        await expect(page.locator("output")).toContainText(JSON.stringify({ user: uid, language }));
        expect(await page.evaluate(() => document.documentElement.lang)).toBe(language);
    }
    await page.reload();
    await page.getByRole("button", { name: "Login inherited", exact: true }).click();
    await expect(page.locator("output")).toContainText(JSON.stringify({ user: "user-inherited", language: "ko-KR" }));
    expect(writes).toBe(0);
});
