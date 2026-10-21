import { expect, test } from "@playwright/test";
const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=";
for (const width of [390, 1440]) {
    test(`bot assistant and standalone avatar share preview and normal creation at ${width}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 950 });
        const calls: { path: string; method: string; body: string }[] = [];
        const errors: string[] = [];
        let previewFailure = false;
        let releasePreview: (() => void) | undefined;
        const pending: { preview?: Promise<void> } = {};
        page.on("pageerror", (error) => errors.push(error.message));
        await page.route("**/bot-avatar-fixture.html", (route) =>
            route.fulfill({
                contentType: "text/html",
                body: `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
            <body><div id="root"></div><script type="module">import RefreshRuntime from "/@react-refresh";
            RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;
            window.__vite_plugin_react_preamble_installed__=true;</script>
            <script type="module" src="/src/pages/SettingsPage/components/bots/bot-avatar.fixture.tsx"></script></body></html>`,
            })
        );
        await page.route("**/*", async (route) => {
            const req = route.request();
            if (!["fetch", "xhr"].includes(req.resourceType())) return route.fallback();
            const path = new URL(req.url()).pathname;
            calls.push({ path, method: req.method(), body: req.postData() ?? "" });
            if (path.endsWith("/image-profiles"))
                return route.fulfill({
                    json: { configured: true, profiles: [{ uid: "image-profile", display_name: "Icon provider", model_name: "image-model" }] },
                });
            if (path.endsWith("/avatar/preview")) {
                if (pending.preview) await pending.preview;
                if (previewFailure) return route.fulfill({ status: 503, json: { message: "Image provider unavailable" } });
                return route.fulfill({ json: { preview_data_url: png, width: 512, height: 512 } }).catch(() => undefined);
            }
            if (path.endsWith("/bot/draft"))
                return route.fulfill({
                    json: {
                        draft: {
                            bot_name: "Tag bot",
                            bot_uname: "tag-bot",
                            suggestions: [],
                            value_patch: {
                                agent_llm: "OpenAI",
                                base_url: "https://text.invalid/v1",
                                model_name: "fixture-model",
                                api_key: "fixture-secret",
                                system_prompt: "Tag cards",
                            },
                        },
                    },
                });
            if (path.endsWith("/settings/bot") && req.method() === "POST")
                return route.fulfill({
                    status: 201,
                    json: {
                        bot: { uid: "created", bot_name: "Tag bot", bot_uname: "tag-bot", platform: "default", platform_running_type: "default" },
                        revealed_app_api_token: "fixture-token",
                    },
                });
            return route.fulfill({
                json: { providers: [], tools: [], api_routes: [], comfort_tools: [], mcp_tools: [], mcp_tool_groups: [], oidc_enabled: false },
            });
        });
        await page.goto("/bot-avatar-fixture.html");
        await page.getByRole("textbox", { name: "API key*", exact: true }).fill("fixture-secret");
        await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
        const avatarDialog = page.getByRole("dialog", { name: "Generate avatar", exact: true });
        await expect(
            avatarDialog.getByRole("option", { name: "Icon provider · image-model" }),
            JSON.stringify({ paths: calls.map((call) => call.path), errors })
        ).toBeAttached();
        await avatarDialog.getByRole("combobox").selectOption("image-profile");
        await expect(avatarDialog.getByRole("button", { name: "Generate with AI", exact: true })).toBeDisabled();
        await avatarDialog.getByRole("checkbox").check();
        await avatarDialog.getByRole("button", { name: "Generate with AI", exact: true }).click();
        await avatarDialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
        await expect(page.getByRole("dialog")).toHaveCount(1);
        await expect(page.locator("img[src^='data:image/png']")).toHaveCount(1);
        expect(calls.some((call) => call.method === "PUT")).toBe(false);
        await page.getByPlaceholder(/Describe the bot to create/).fill("Make a card tagging bot");
        await page.getByRole("button", { name: "Create draft", exact: true }).click();
        const assistantAvatar = page.getByRole("dialog", { name: "Generate avatar", exact: true });
        await expect(assistantAvatar.getByRole("textbox", { name: "Prompt sent to the selected model", exact: true })).toHaveValue(/Tag bot/);
        expect(calls.filter((call) => call.path.endsWith("/avatar/preview"))).toHaveLength(1);
        await assistantAvatar.getByRole("combobox").selectOption("image-profile");
        await assistantAvatar.getByRole("checkbox").check();
        await assistantAvatar.getByRole("button", { name: "Generate with AI", exact: true }).click();
        await expect(assistantAvatar.getByRole("button", { name: "Apply generated avatar", exact: true })).toBeVisible();
        await page.screenshot({ path: testInfo.outputPath("bot-avatar-preview.png") });
        await assistantAvatar.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
        const botName = page.getByRole("textbox", { name: "Bot name*", exact: true });
        await expect(botName).toHaveValue("Tag bot");
        await botName.fill("User edited bot");

        // An image-provider failure must preserve the already accepted text draft.
        previewFailure = true;
        await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
        await avatarDialog.getByRole("combobox").selectOption("image-profile");
        await avatarDialog.getByRole("checkbox").check();
        await avatarDialog.getByRole("button", { name: "Generate with AI", exact: true }).click();
        await expect(avatarDialog.getByRole("alert")).toBeVisible();
        await expect(avatarDialog.getByRole("checkbox")).not.toBeChecked();
        await expect(avatarDialog.getByRole("button", { name: "Apply generated avatar", exact: true })).toHaveCount(0);
        await avatarDialog.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(botName).toHaveValue("User edited bot");

        // Close an in-flight generation, upload a different file, then deliver the stale response.
        previewFailure = false;
        pending.preview = new Promise<void>((resolve) => (releasePreview = resolve));
        await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
        await avatarDialog.getByRole("combobox").selectOption("image-profile");
        await avatarDialog.getByRole("checkbox").check();
        await avatarDialog.getByRole("button", { name: "Generate with AI", exact: true }).click();
        await expect.poll(() => calls.filter((call) => call.path.endsWith("/avatar/preview")).length).toBe(4);
        await page.keyboard.press("Escape");
        await expect(avatarDialog).toHaveCount(0);
        await page.locator("input[type=file]").setInputFiles({
            name: "direct-avatar.png",
            mimeType: "image/png",
            buffer: Buffer.from(png.split(",")[1], "base64"),
        });
        releasePreview?.();
        await expect(botName).toHaveValue("User edited bot");
        await page.getByRole("textbox", { name: "API key*", exact: true }).fill("fixture-secret");
        await page.getByRole("button", { name: "Create", exact: true }).click();
        await expect
            .poll(() => calls.find((call) => call.path.endsWith("/settings/bot") && call.method === "POST")?.body ?? "")
            .toContain("direct-avatar.png");
        expect(calls.find((call) => call.path.endsWith("/settings/bot") && call.method === "POST")?.body).not.toContain("generated-avatar.png");
        const imageCalls = calls.filter((call) => call.path.endsWith("/avatar/preview"));
        expect(imageCalls).toHaveLength(4);
        expect(imageCalls.every((call) => !call.body.includes("fixture-secret") && !call.body.includes("text.invalid"))).toBe(true);
        expect(calls.some((call) => call.path.includes("/account/avatar") || call.method === "PUT")).toBe(false);
        expect(errors).toEqual([]);
    });
}
