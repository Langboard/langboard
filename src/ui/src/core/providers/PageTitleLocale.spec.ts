import { expect, test } from "@playwright/test";

test("Korean sign-in page sets a translated document title", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("lang", "ko-KR"));
    await page.goto("/src/core/providers/PageTitleLocale.fixture.html");
    await expect(page.getByRole("heading", { name: "로그인", exact: true })).toBeVisible();
    await expect(page).toHaveTitle("로그인 - Langboard");
    expect(errors).toEqual([]);
});

test("production static titles react across four languages while raw aliases remain verbatim", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/src/core/providers/PageTitleLocale.fixture.html");
    const titles: Record<string, string[]> = {
        signin: ["Sign in", "로그인", "ログイン", "登录"],
        "verify-email": ["Email verification", "이메일 인증", "メールアドレスの確認", "邮箱验证"],
        preferences: ["Preferences", "환경설정", "環境設定", "偏好设置"],
        keys: ["API keys", "API 키", "APIキー", "API 密钥"],
        labels: ["Global labels", "전역 레이블", "グローバルラベル", "全局标签"],
        dashboard: ["Dashboard", "대시보드", "ダッシュボード", "仪表盘"],
    };
    let counters: Record<string, number>;
    for (const [screen, names] of Object.entries(titles)) {
        await page.getByRole("button", { name: `screen:${screen}`, exact: true }).click();
        await expect(page).toHaveTitle(/ - Langboard$/);
        counters = await page.evaluate(() => ({ ...(window as unknown as { titleFixtureCalls: Record<string, number> }).titleFixtureCalls }));
        if (screen === "verify-email") expect(counters.mutation).toBe(1);
        if (screen === "labels") expect(counters.load).toBe(1);
        if (screen === "dashboard") expect(counters.subscription).toBe(1);
        for (const [index, locale] of ["en-US", "ko-KR", "ja-JP", "zh-CN"].entries()) {
            await page.getByRole("button", { name: locale, exact: true }).click();
            await expect(page).toHaveTitle(`${names[index]} - Langboard`);
            expect(
                await page.evaluate(() => ({ ...(window as unknown as { titleFixtureCalls: Record<string, number> }).titleFixtureCalls }))
            ).toEqual(counters);
        }
    }
    expect(errors).toEqual([]);
    for (const name of ["Sign In", "Dashboard", "settings.API keys"]) {
        await page.getByRole("button", { name: `screen:${name}`, exact: true }).click();
        for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
            await page.getByRole("button", { name: locale, exact: true }).click();
            await expect(page).toHaveTitle(`${name} - Langboard`);
        }
    }
});
