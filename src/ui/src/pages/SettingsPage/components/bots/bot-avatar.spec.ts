import { expect, test } from "@playwright/test";
const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=";
for (const width of [390, 1440]) {
    for (const theme of ["light", "dark"]) {
        test(`bot assistant and standalone avatar share preview and normal creation at ${width} in ${theme}`, async ({ page }, testInfo) => {
            await page.setViewportSize({ width, height: 950 });
            const calls: { path: string; method: string; body: string }[] = [];
            const errors: string[] = [];
            let previewFailure = false;
            let releaseDraft: (() => void) | undefined;
            const draftPending = new Promise<void>((resolve) => (releaseDraft = resolve));
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
                if (path.endsWith("/bot/draft")) {
                    await draftPending;
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
                }
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
            await page.evaluate((theme) => document.documentElement.classList.toggle("dark", theme === "dark"), theme);
            await page.getByRole("textbox", { name: "API key*", exact: true }).fill("fixture-secret");
            await page.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            const avatarDialog = page.getByRole("dialog", { name: "Generate avatar", exact: true });
            await expect(
                avatarDialog.getByRole("option", { name: "Icon provider · image-model" }),
                JSON.stringify({ paths: calls.map((call) => call.path), errors })
            ).toBeAttached();
            await avatarDialog.getByRole("combobox").selectOption("image-profile");
            await expect(avatarDialog.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true })).toBeDisabled();
            await avatarDialog.getByRole("checkbox").check();
            await avatarDialog.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await avatarDialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
            await expect(page.getByRole("dialog")).toHaveCount(1);
            await expect(page.locator("img[src^='data:image/png']")).toHaveCount(1);
            expect(calls.some((call) => call.method === "PUT")).toBe(false);
            await page.getByPlaceholder(/Describe the bot to create/).fill("Make a card tagging bot");
            await page.getByRole("button", { name: "Create draft", exact: true }).click();
            const assistantAvatar = page.getByRole("dialog", { name: "Generate avatar", exact: true });
            await expect(assistantAvatar.getByRole("textbox", { name: "Prompt sent to the selected model", exact: true })).toHaveValue(
                /Make a card tagging bot/
            );
            expect(calls.filter((call) => call.path.endsWith("/avatar/preview"))).toHaveLength(1);
            expect(calls.filter((call) => call.path.endsWith("/bot/draft"))).toHaveLength(0);
            await assistantAvatar.getByRole("combobox").selectOption("image-profile");
            await assistantAvatar.getByRole("checkbox").check();
            await assistantAvatar.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await expect(assistantAvatar.getByRole("button", { name: "Apply generated avatar", exact: true })).toBeVisible();
            expect(calls.filter((call) => call.path.endsWith("/bot/draft"))).toHaveLength(1);
            // Image response completes while text draft remains in flight.
            await page.screenshot({ path: testInfo.outputPath(`bot-avatar-preview-${theme}-${width}.png`) });
            await assistantAvatar.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
            await expect(page.getByRole("button", { name: "Create", exact: true })).toBeDisabled();
            releaseDraft?.();
            const botName = page.getByRole("textbox", { name: "Bot name*", exact: true });
            await expect(botName).toHaveValue("Tag bot");
            await expect(avatarDialog).toHaveCount(0);
            await botName.fill("User edited bot");

            // Assistant image failure must preserve its successful text draft.
            previewFailure = true;
            await page.getByPlaceholder(/Describe the bot to create/).fill("Make a card tagging bot");
            await page.getByRole("button", { name: "Create draft", exact: true }).click();
            await avatarDialog.getByRole("combobox").selectOption("image-profile");
            await avatarDialog.getByRole("checkbox").check();
            await avatarDialog.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await expect(avatarDialog.getByRole("alert")).toBeVisible();
            await expect(avatarDialog.getByRole("checkbox")).not.toBeChecked();
            await expect(avatarDialog.getByRole("button", { name: "Apply generated avatar", exact: true })).toHaveCount(0);
            await avatarDialog.getByRole("button", { name: "Cancel", exact: true }).click();
            await expect(botName).toHaveValue("Tag bot");
            await botName.fill("User edited bot");

            // Close an in-flight generation, upload a different file, then deliver the stale response.
            previewFailure = false;
            pending.preview = new Promise<void>((resolve) => (releasePreview = resolve));
            await page.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await avatarDialog.getByRole("combobox").selectOption("image-profile");
            await avatarDialog.getByRole("checkbox").check();
            await avatarDialog.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await expect.poll(() => calls.filter((call) => call.path.endsWith("/avatar/preview")).length).toBe(4);
            await page.keyboard.press("Escape");
            await expect(avatarDialog).toHaveCount(0);
            await page.locator("input[type=file]").setInputFiles({
                name: "direct-avatar.png",
                mimeType: "image/png",
                buffer: Buffer.from(png.split(",")[1], "base64"),
            });
            releasePreview?.();

            // A manual upload also invalidates generation while its dialog is still open.
            pending.preview = new Promise<void>((resolve) => (releasePreview = resolve));
            await page.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await avatarDialog.getByRole("combobox").selectOption("image-profile");
            await avatarDialog.getByRole("checkbox").check();
            await avatarDialog.getByRole("button", { name: /^(Generate with AI|Generate draft and avatar)$/, exact: true }).click();
            await expect.poll(() => calls.filter((call) => call.path.endsWith("/avatar/preview")).length).toBe(5);
            await page.locator("input[type=file]").setInputFiles({
                name: "direct-avatar.png",
                mimeType: "image/png",
                buffer: Buffer.from(png.split(",")[1], "base64"),
            });
            releasePreview?.();
            await expect(avatarDialog.getByRole("checkbox")).not.toBeChecked();
            await expect(avatarDialog.getByRole("button", { name: "Apply generated avatar", exact: true })).toHaveCount(0);
            await avatarDialog.getByRole("button", { name: "Cancel", exact: true }).click();
            await expect(botName).toHaveValue("User edited bot");
            await page.getByRole("textbox", { name: "API key*", exact: true }).fill("fixture-secret");
            await page.getByRole("button", { name: "Create", exact: true }).click();
            await expect
                .poll(() => calls.find((call) => call.path.endsWith("/settings/bot") && call.method === "POST")?.body ?? "")
                .toContain("direct-avatar.png");
            expect(calls.find((call) => call.path.endsWith("/settings/bot") && call.method === "POST")?.body).not.toContain("generated-avatar.png");
            const imageCalls = calls.filter((call) => call.path.endsWith("/avatar/preview"));
            expect(imageCalls).toHaveLength(5);
            expect(imageCalls.every((call) => !call.body.includes("fixture-secret") && !call.body.includes("text.invalid"))).toBe(true);
            expect(calls.some((call) => call.path.includes("/account/avatar") || call.method === "PUT")).toBe(false);
            expect(errors).toEqual([]);
        });
    }
}

for (const scenario of [
    "generated multipart",
    "manual edit",
    "close reopen",
    "actor change",
    "authority change",
    "text failure",
    "text only",
] as const) {
    test(`assistant isolation: ${scenario}`, async ({ page }) => {
        let release: (() => void) | undefined;
        const pending = new Promise<void>((resolve) => (release = resolve));
        const calls: { path: string; body: string }[] = [];
        await page.route("**/bot-avatar-fixture.html", (route) =>
            route.fulfill({
                contentType: "text/html",
                body: `<!doctype html><html><body><div id="root"></div><script type="module">
                    import RefreshRuntime from "/@react-refresh";
                    RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;
                    window.__vite_plugin_react_preamble_installed__=true;</script>
                    <script type="module" src="/src/pages/SettingsPage/components/bots/bot-avatar.fixture.tsx"></script></body></html>`,
            })
        );
        await page.route("**/*", async (route) => {
            const request = route.request();
            if (!["fetch", "xhr"].includes(request.resourceType())) return route.fallback();
            const path = new URL(request.url()).pathname;
            calls.push({ path, body: request.postData() ?? "" });
            if (path.endsWith("/image-profiles"))
                return route.fulfill({
                    json: { profiles: scenario === "text only" ? [] : [{ uid: "image-profile", display_name: "Icons", model_name: "images" }] },
                });
            if (path.endsWith("/avatar/preview")) return route.fulfill({ json: { preview_data_url: png } });
            if (path.endsWith("/bot/draft")) {
                await pending;
                return route
                    .fulfill(
                        scenario === "text failure"
                            ? { status: 503, json: { message: "Unavailable" } }
                            : {
                                  json: {
                                      draft: {
                                          bot_name: "AI result",
                                          bot_uname: "ai-result",
                                          value_patch: {
                                              agent_llm: "OpenAI",
                                              base_url: "https://text.invalid/v1",
                                              model_name: "fixture-model",
                                              api_key: "fixture-secret",
                                              system_prompt: "Tag cards",
                                          },
                                          suggestions: [],
                                      },
                                  },
                              }
                    )
                    .catch(() => undefined);
            }
            if (path.endsWith("/settings/bot"))
                return route.fulfill({
                    status: 201,
                    json: {
                        bot: { uid: "created", bot_name: "AI result", bot_uname: "ai-result", platform: "default", platform_running_type: "default" },
                        revealed_app_api_token: "fixture-token",
                    },
                });
            return route.fulfill({
                json: { providers: [], tools: [], api_routes: [], comfort_tools: [], mcp_tools: [], mcp_tool_groups: [], oidc_enabled: false },
            });
        });
        await page.goto("/bot-avatar-fixture.html");
        const botName = page.getByRole("textbox", { name: "Bot name*", exact: true });
        await botName.fill("Original");
        await page.getByRole("textbox", { name: "API key*", exact: true }).fill("fixture-secret");
        await page.getByPlaceholder(/Describe the bot to create/).fill("A helpful bot");
        await page.getByRole("button", { name: "Create draft", exact: true }).click();
        const avatar = page.getByRole("dialog", { name: "Generate avatar", exact: true });
        await expect(avatar.getByRole("combobox")).toBeVisible();
        expect(calls.filter((call) => call.path.endsWith("/bot/draft"))).toHaveLength(0);
        if (scenario === "text only") {
            await avatar.getByRole("button", { name: "Create text draft only", exact: true }).click();
        } else {
            await avatar.getByRole("combobox").selectOption("image-profile");
            await avatar.getByRole("checkbox").check();
            await avatar.getByRole("button", { name: "Generate draft and avatar", exact: true }).click();
            await expect(avatar.getByRole("button", { name: "Apply generated avatar", exact: true })).toBeVisible();
            expect(calls.some((call) => call.path.endsWith("/settings/bot"))).toBe(false);
            await avatar.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
        }
        await expect(page.getByRole("button", { name: "Create", exact: true })).toBeDisabled();
        if (scenario === "manual edit") await botName.fill("Manual choice");
        if (scenario === "close reopen") {
            await page.getByRole("button", { name: "Cancel", exact: true }).click();
            await page.getByRole("button", { name: "Reopen creation", exact: true }).click();
            await botName.fill("New session");
        }
        if (scenario === "actor change")
            await page
                .getByRole("button", { name: "Change actor", exact: true, includeHidden: true })
                .evaluate((element: HTMLButtonElement) => element.click());
        if (scenario === "authority change")
            await page
                .getByRole("button", { name: "Revoke authority", exact: true, includeHidden: true })
                .evaluate((element: HTMLButtonElement) => element.click());
        release?.();
        await expect(page.getByRole("button", { name: "Create", exact: true })).toBeEnabled();
        if (scenario === "generated multipart") {
            await expect(botName).toHaveValue("AI result");
            await page.getByRole("button", { name: "Create", exact: true }).click();
            await expect
                .poll(() => calls.find((call) => call.path.endsWith("/settings/bot"))?.body ?? "")
                .toMatch(/filename="generated-avatar\.png"/);
            expect(calls.find((call) => call.path.endsWith("/settings/bot"))?.body).toContain("Content-Type: image/png");
        } else if (scenario === "manual edit") await expect(botName).toHaveValue("Manual choice");
        else if (scenario === "close reopen") await expect(botName).toHaveValue("New session");
        else if (scenario === "actor change" || scenario === "authority change" || scenario === "text failure")
            await expect(botName).toHaveValue("Original");
        else {
            await expect(botName).toHaveValue("AI result");
            expect(calls.some((call) => call.path.endsWith("/avatar/preview"))).toBe(false);
        }
    });
}
