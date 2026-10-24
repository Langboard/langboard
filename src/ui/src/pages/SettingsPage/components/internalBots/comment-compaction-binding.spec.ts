import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
    test(`compaction role reuses native provider input and excludes workflow platforms at ${width}px`, async ({ page }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({ width, height: 900 });
        await page.route("**/schema/api**", (route) => route.fulfill({ json: { apis: {}, api_comfort_tools: [] } }));
        await page.goto("/src/pages/SettingsPage/components/internalBots/provider-role.fixture.html");
        await page.evaluate(async () => {
            const modulePath = "/src/i18n.ts";
            await (await import(modulePath)).default.changeLanguage("en-US");
        });
        await page.getByRole("button", { name: "Open creation", exact: true }).click();
        const dialog = page.getByRole("dialog");
        await dialog
            .getByRole("combobox")
            .filter({ hasText: /Select a platform\*?$/ })
            .click();
        await page.getByRole("option", { name: "N8N", exact: true }).click();
        await dialog.getByRole("combobox").filter({ hasText: "Select a type" }).click();
        await page.getByRole("option", { name: "Comment compaction", exact: true }).click();
        await expect(dialog.getByRole("combobox").filter({ hasText: "Select a platform" })).toHaveCount(0);
        await expect(dialog.getByRole("combobox").filter({ hasText: "Select a running type" })).toHaveCount(0);
        await expect(dialog.getByText("Z.ai Coding Plan", { exact: true }).first()).toBeVisible();
        await expect(dialog.getByRole("textbox", { name: "Internal bot display name*", exact: true })).toHaveValue("컴팩터");
        await expect(dialog.getByRole("textbox", { name: "Base URL*", exact: true })).toHaveValue("https://api.z.ai/api/coding/paas/v4");
        await expect(dialog.getByRole("textbox", { name: "Model*", exact: true })).toHaveValue("glm-5.3");
        await expect(dialog.getByText("Comfort tools", { exact: true })).toHaveCount(0);
        await expect(dialog.getByText("System prompt", { exact: true })).toHaveCount(0);
        expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
        await page.screenshot({ path: `.verification/compactor-profile-${width}.png`, fullPage: true });
        let submitted: Record<string, string> | undefined;
        await page.route("**/settings/internal-bot", async (route) => {
            const body = new Uint8Array(route.request().postDataBuffer()!);
            const fields = await new Response(body.buffer, { headers: { "content-type": route.request().headers()["content-type"] } }).formData();
            submitted = Object.fromEntries([...fields.entries()].filter((entry): entry is [string, string] => typeof entry[1] === "string"));
            await route.fulfill({ status: 201, json: { internal_bot: { uid: "created-compaction-profile" } } });
        });
        await dialog.getByRole("textbox", { name: "API key*", exact: true }).fill("local-fixture-provider-key");
        await dialog.getByRole("textbox", { name: "Model*", exact: true }).fill("compact-fixture-model");
        await dialog.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => submitted?.bot_type).toBe("comment_compaction");
        expect(submitted?.platform).toBe("default");
        expect(submitted?.platform_running_type).toBe("default");
        expect(submitted?.display_name).toBe("컴팩터");
        expect(JSON.parse(submitted!.value).agent_llm).toBe("Z.ai Coding Plan");
        expect(JSON.parse(submitted!.value).api_names ?? []).toEqual([]);
        expect(JSON.parse(submitted!.value).model_name).toBe("compact-fixture-model");
        await expect(dialog).toHaveCount(0);
    });
}
