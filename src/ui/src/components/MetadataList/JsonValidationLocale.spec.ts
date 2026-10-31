import { expect, test } from "@playwright/test";

const fixtureModule = "/src/components/MetadataList/JsonValidationLocale.fixture.tsx";

for (const surface of ["bot", "metadata"] as const) {
    test(`${surface} JSON errors follow language changes and recover without exposing parser details`, async ({ page }) => {
        await page.addInitScript(() => localStorage.setItem("lang", "en-US"));
        // Isolate collaboration transport; never connect this fixture to live sockets.
        await page.route("**/src/components/Collaborative/index.ts*", (route) =>
            route.fulfill({
                contentType: "application/javascript",
                body: `export { CollaborativeFixture as default } from ${JSON.stringify(fixtureModule)};`,
            })
        );
        await page.goto("/src/components/MetadataList/JsonValidationLocale.fixture.html");
        if (surface === "metadata") await page.getByTestId("metadata").getByRole("button").click();
        const container = surface === "bot" ? page.getByTestId("bot") : page.getByRole("dialog");
        const input = container.locator("textarea:not([readonly])");
        const error = container.locator("textarea[readonly]");
        const invalid = JSON.stringify({ PRIVATE_PARSER_DIAGNOSTIC: "" }).slice(0, -3);
        await input.fill(invalid);
        for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN", "en-US"]) {
            // The metadata dialog is modal; switch through the same native i18n instance.
            await page.evaluate(async (value) => {
                const { default: i18n } = await import(String("/src/i18n.ts"));
                await i18n.changeLanguage(value);
            }, locale);
            const expected = await page.getByTestId("expected-error").innerText();
            await expect(error).toHaveValue(expected);
            await expect(error).not.toHaveValue(/PRIVATE_PARSER_DIAGNOSTIC|SyntaxError|position|Unexpected token/);
            await expect(input).toHaveValue(invalid);
            await input.fill(JSON.stringify({ original_user_key: "unchanged 日本語" }));
            await expect(error).toHaveCount(0);
            await expect(input).toHaveValue(/unchanged 日本語/);
            await input.fill("plain invalid JSON");
            await expect(error).toHaveValue(expected);
            await input.fill(invalid);
            await expect(error).toHaveValue(expected);
        }
    });
}
