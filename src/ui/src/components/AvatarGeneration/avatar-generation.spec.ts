import { expect, test } from "@playwright/test";
const fixture = "/.verification/avatar-generation-ui-20261019/fixture.html";
const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=";
for (const target of ["personal", "board"]) {
    test(`${target} avatar generation timeout preserves current avatar and permits explicit retry`, async ({ page }, testInfo) => {
        let previewCalls = 0;
        let applyCalls = 0;
        await page.route("**/*", async (route) => {
            const request = route.request();
            if (!["fetch", "xhr"].includes(request.resourceType())) return route.continue();
            const url = new URL(request.url()).pathname;
            if (url.endsWith("/image-profiles"))
                return route.fulfill({
                    json: { configured: true, profiles: [{ uid: "profile", display_name: "Icon provider", model_name: "image-model" }] },
                });
            if (url.endsWith("/preview")) {
                previewCalls++;
                if (previewCalls === 1) return route.fulfill({ status: 504, json: { error: "avatar_generation_timeout" } });
                return route.fulfill({ json: { preview_data_url: png, width: 512, height: 512 } });
            }
            if (url.endsWith("/avatar")) {
                if (request.method() === "PUT") {
                    applyCalls++;
                }
                return route.fulfill({
                    json: { avatar: png, previous_avatar: null, avatar_revision: 1, can_restore: false },
                });
            }
            return route.fulfill({ json: { oidc_enabled: false } });
        });
        await page.goto(fixture);
        if (target === "board") await page.getByRole("button", { name: "Toggle target", exact: true }).click();
        await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
        const dialog = page.getByRole("dialog");
        const profile = dialog.getByRole("combobox", { name: "Image profile", exact: true });
        const prompt = dialog.getByRole("textbox", { name: "Prompt sent to the selected model", exact: true });
        const consent = dialog.getByRole("checkbox", { name: /Send this prompt to the selected external image provider/ });
        const generate = dialog.getByRole("button", { name: "Generate with AI", exact: true });
        await profile.selectOption("profile");
        const currentAvatar = page.locator("main img");
        const originalAvatars = await currentAvatar.evaluateAll((images) => images.map((image) => image.getAttribute("src")));
        await prompt.fill("Keep this revised icon prompt after timeout");
        await consent.check();
        await generate.click();
        await expect(dialog.getByRole("alert")).toHaveText(
            "Generation timed out. Your current avatar is unchanged. The provider may still charge for the request. No automatic retry was made."
        );
        expect(await currentAvatar.evaluateAll((images) => images.map((image) => image.getAttribute("src")))).toEqual(originalAvatars);
        await expect(prompt).toHaveValue("Keep this revised icon prompt after timeout");
        await expect(profile).toHaveValue("profile");
        await expect(dialog.locator("img")).toHaveCount(0);
        await expect(dialog.getByRole("button", { name: "Apply generated avatar", exact: true })).toHaveCount(0);
        await expect(consent).not.toBeChecked();
        await expect(generate).toBeDisabled();
        expect(previewCalls).toBe(1);
        expect(applyCalls).toBe(0);
        await page.screenshot({ path: testInfo.outputPath(`${target}-timeout.png`), fullPage: true });
        await consent.check();
        await generate.click();
        await expect(dialog.getByRole("alert")).toHaveCount(0);
        await expect(dialog.locator("img")).toHaveCount(6);
        expect(await currentAvatar.evaluateAll((images) => images.map((image) => image.getAttribute("src")))).toEqual(originalAvatars);
        await expect(prompt).toHaveValue("Keep this revised icon prompt after timeout");
        expect(previewCalls).toBe(2);
        expect(applyCalls).toBe(0);
    });
}

for (const width of [390, 1440]) {
    test(`personal and board explicit durable avatar lifecycle at ${width}`, async ({ page }, testInfo) => {
        const calls: { url: string; method: string; body: string }[] = [];
        let revision = 0;
        let avatar: string | null = null;
        let previous: string | null = null;
        let failApply = true;
        await page.route("**/*", async (route) => {
            const request = route.request();
            if (!["fetch", "xhr"].includes(request.resourceType())) return route.continue();
            const url = new URL(request.url()).pathname;
            calls.push({ url, method: request.method(), body: request.postData() ?? "" });
            if (url.endsWith("/image-profiles"))
                return route.fulfill({
                    json: { configured: true, profiles: [{ uid: "profile", display_name: "Icon provider", model_name: "image-model" }] },
                });
            if (url.endsWith("/preview")) return route.fulfill({ json: { preview_data_url: png, width: 512, height: 512 } });
            if (url.endsWith("/context"))
                return route.fulfill({
                    json: {
                        source_revision: "reviewed-1",
                        sources: [
                            { id: "title", kind: "project_title", text: "Mountain navigation" },
                            { id: "card", kind: "card_title", text: "Sensitive draft" },
                        ],
                        profiles: [{ uid: "summary", display_name: "Summary model", model_name: "text-model" }],
                        external_request: false,
                    },
                });
            if (url.endsWith("/semantic-preview"))
                return route.fulfill({
                    json: {
                        external_request: true,
                        source_revision: "reviewed-1",
                        profile_uid: "summary",
                        prompt: "A mountain compass for navigation",
                    },
                });
            if (url.endsWith("/restore")) {
                const swap = avatar;
                avatar = previous;
                previous = swap;
                revision++;
                return route.fulfill({ json: { avatar, previous_avatar: previous, avatar_revision: revision, can_restore: true } });
            }
            if (url.endsWith("/avatar")) {
                if (request.method() === "PUT") {
                    if (failApply) return route.fulfill({ status: 500, json: { detail: "redacted upstream" } });
                    previous = avatar;
                    avatar = png;
                    revision++;
                }
                return route.fulfill({ json: { avatar, previous_avatar: previous, avatar_revision: revision, can_restore: revision > 0 } });
            }
            return route.fulfill({ json: { oidc_enabled: false } });
        });
        await page.setViewportSize({ width, height: 1000 });
        await page.goto(fixture);
        await expect(page.getByRole("button", { name: "Generate with AI", exact: true })).toBeVisible();
        expect(calls.filter((call) => call.url.includes("/avatar"))).toHaveLength(0);
        // A direct-upload draft must be removed only after persisted AI apply readback.
        await page
            .locator("input[type=file]")
            .first()
            .setInputFiles({ name: "old-upload.png", mimeType: "image/png", buffer: Buffer.from(png.split(",")[1], "base64") });
        await page.getByRole("textbox", { name: "First Name", exact: true }).fill("Unsaved first name");
        for (const board of [false, true]) {
            if (board) await page.getByRole("button", { name: "Toggle target", exact: true }).click();
            await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
            const dialog = page.getByRole("dialog");
            await expect(dialog).toBeVisible();
            await dialog.getByRole("combobox", { name: "Image profile", exact: true }).selectOption("profile");
            const prompt = dialog.getByRole("textbox", { name: "Prompt sent to the selected model", exact: true });
            if (board) {
                const count = calls.filter((call) => call.url.endsWith("/preview")).length;
                await dialog.getByRole("button", { name: "Prepare board summary", exact: true }).click();
                await expect(dialog.getByText("Mountain navigation", { exact: true })).toBeVisible();
                expect(calls.filter((call) => call.url.endsWith("/semantic-preview"))).toHaveLength(0);
                expect(calls.filter((call) => call.url.endsWith("/preview"))).toHaveLength(count);
                await dialog.getByRole("checkbox", { name: "Sensitive draft", exact: true }).uncheck();
                await dialog.getByRole("combobox", { name: "Avatar summary profile", exact: true }).selectOption("summary");
                const summarize = dialog.getByRole("button", { name: "Generate semantic icon prompt", exact: true });
                await expect(summarize).toBeDisabled();
                await dialog.getByRole("checkbox", { name: /Send exactly the checked text sources/ }).check();
                await summarize.click();
                await expect(prompt).toHaveValue("A mountain compass for navigation");
                const semantic = calls.filter((call) => call.url.endsWith("/semantic-preview")).at(-1)!;
                expect(JSON.parse(semantic.body)).toEqual({
                    source_revision: "reviewed-1",
                    profile_uid: "summary",
                    source_ids: ["title"],
                    confirmed: true,
                });
                await expect(dialog.getByRole("button", { name: "Generate with AI", exact: true })).toBeDisabled();
                await dialog.getByRole("checkbox", { name: "Sensitive draft", exact: true }).check();
                await expect(prompt).not.toHaveValue("A mountain compass for navigation");
            }
            await prompt.fill("A blue simple geometric mountain icon");
            const consent = dialog.getByRole("checkbox", { name: /Send this prompt to the selected external image provider/ });
            const generate = dialog.getByRole("button", { name: "Generate with AI", exact: true });
            await expect(generate).toBeDisabled();
            await consent.check();
            await generate.click();
            await expect(dialog.locator("img")).toHaveCount(6);
            await expect(consent).not.toBeChecked();
            await expect(generate).toBeDisabled();
            const previewCall = calls.filter((call) => call.url.endsWith("/preview")).at(-1)!;
            expect(JSON.parse(previewCall.body)).toEqual({
                profile_uid: "profile",
                prompt: "A blue simple geometric mountain icon",
                confirmed: true,
            });
            await page.screenshot({ path: testInfo.outputPath(`avatar-${width}-${board ? "board" : "personal"}-preview.png`), fullPage: true });
            const appliesBefore = calls.filter((call) => call.method === "PUT" && call.url.endsWith("/avatar")).length;
            expect(appliesBefore).toBe(board ? 2 : 0);
            await dialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
            if (!board) {
                await expect(dialog.getByRole("alert")).toContainText("Could not complete");
                await expect(dialog.locator("img")).toHaveCount(6);
                failApply = false;
                await dialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
            }
            await expect(dialog.locator("img")).toHaveCount(0);
            const applied = calls.filter((call) => call.method === "PUT" && call.url.endsWith("/avatar")).at(-1)!;
            expect(applied.body).toMatch(/name="expected_revision"/);
            expect(applied.body).toMatch(/filename="generated-avatar.png"/);
            await expect(dialog.getByRole("button", { name: "Restore previous avatar", exact: true })).toBeDisabled();
            await dialog.getByRole("checkbox", { name: "Restore the previous saved avatar.", exact: true }).check();
            await dialog.getByRole("button", { name: "Restore previous avatar", exact: true }).click();
            await expect(dialog.getByRole("checkbox", { name: "Restore the previous saved avatar.", exact: true })).not.toBeChecked();
            await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
            if (!board) {
                await expect(page.getByRole("textbox", { name: "First Name", exact: true })).toHaveValue("Unsaved first name");
                await page.getByRole("button", { name: "Save", exact: true }).click();
                await expect.poll(() => calls.some((call) => call.url.endsWith("/account/profile"))).toBe(true);
                const profile = calls.find((call) => call.url.endsWith("/account/profile"))!;
                expect(profile.body).not.toContain("old-upload.png");
                expect(profile.body).toMatch(/name="expected_avatar_revision"[\s\S]*?2/);
            }
        }
        expect(calls.filter((call) => call.url.endsWith("/preview"))).toHaveLength(2);
    });
}

test("unconfigured nonadmin, stale revision, cancellation and authority changes", async ({ page }) => {
    let configured = false;
    let conflict = false;
    let hold = false;
    let release: (() => void) | undefined;
    let previewCalls = 0;
    let putCalls = 0;
    let revision = 0;
    await page.route("**/*", async (route) => {
        if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
        const url = new URL(route.request().url()).pathname;
        if (url.endsWith("/image-profiles"))
            return route.fulfill({
                json: { configured, profiles: configured ? [{ uid: "profile", display_name: "Provider", model_name: "image-model" }] : [] },
            });
        if (url.endsWith("/preview")) {
            previewCalls++;
            if (hold)
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            return route.fulfill({ json: { preview_data_url: png } }).catch(() => undefined);
        }
        if (url.endsWith("/avatar")) {
            if (route.request().method() === "PUT") {
                putCalls++;
                if (conflict) {
                    revision++;
                    return route.fulfill({ status: 409, json: { error: "avatar_revision_conflict" } });
                }
            }
            return route.fulfill({ json: { avatar: null, previous_avatar: null, avatar_revision: revision, can_restore: false } });
        }
        return route.fulfill({ json: { oidc_enabled: false } });
    });
    await page.goto(fixture);
    await page.getByRole("button", { name: "Toggle target", exact: true }).click();
    const opener = page.getByRole("button", { name: "Generate with AI", exact: true });
    await opener.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText(/No enabled image profile/)).toBeVisible();
    await expect(dialog.getByRole("link", { name: "Image profile settings" })).toHaveCount(0);
    expect(previewCalls).toBe(0);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    configured = true;
    await opener.click();
    const choose = dialog.getByRole("combobox", { name: "Image profile", exact: true });
    await choose.selectOption("profile");
    const prompt = dialog.getByRole("textbox", { name: "Prompt sent to the selected model", exact: true });
    const consent = dialog.getByRole("checkbox", { name: /Send this prompt to the selected external image provider/ });
    const generate = dialog.getByRole("button", { name: "Generate with AI", exact: true });
    await prompt.fill("Retain unsaved prompt");
    await consent.check();
    await generate.click();
    await expect(dialog.locator("img")).toHaveCount(6);
    conflict = true;
    await dialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("Current state was refreshed");
    await expect(prompt).toHaveValue("Retain unsaved prompt");
    await expect(dialog.locator("img")).toHaveCount(0);
    expect(putCalls).toBe(1);
    hold = true;
    for (const action of ["cancel", "Toggle permission", "Switch actor"]) {
        await choose.selectOption("profile");
        await prompt.fill("Manual icon");
        await consent.check();
        const before = previewCalls;
        await generate.click();
        await expect.poll(() => previewCalls).toBe(before + 1);
        if (action === "cancel") await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
        else {
            // External authority changes occur while the modal owns focus.
            await page
                .locator("nav button")
                .filter({ hasText: new RegExp(`^${action}$`) })
                .dispatchEvent("click");
        }
        release?.();
        await expect(dialog.locator("img")).toHaveCount(0);
        await expect(consent).not.toBeChecked();
        if (action === "Toggle permission")
            await page
                .locator("nav button")
                .filter({ hasText: new RegExp(`^${action}$`) })
                .dispatchEvent("click");
    }
    await expect(prompt).not.toHaveValue("Manual icon");
});

test("ordinary direct upload keeps the revision captured at selection", async ({ page }) => {
    let submitted = "";
    await page.route("**/*", async (route) => {
        if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
        if (new URL(route.request().url()).pathname.endsWith("/account/profile")) submitted = route.request().postData() ?? "";
        await route.fulfill({ json: { oidc_enabled: false } });
    });
    await page.goto(fixture);
    await page
        .locator("input[type=file]")
        .first()
        .setInputFiles({ name: "direct.png", mimeType: "image/png", buffer: Buffer.from(png.split(",")[1], "base64") });
    await page.getByRole("button", { name: "Advance avatar revision", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => submitted).toContain("direct.png");
    expect(submitted).toMatch(/name="expected_avatar_revision"[\s\S]*?0/);
});

for (const roles of ["missing", "granted"]) {
    test(`partial admin identity preserves board and profile settings with roles=${roles}`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.route("**/avatar-role-regression.html*", (route) =>
            route.fulfill({
                contentType: "text/html",
                body: `<!doctype html><html>
                    <head><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
                    <body><div id="root"></div><script type="module">
                    import RefreshRuntime from "/@react-refresh";
                    RefreshRuntime.injectIntoGlobalHook(window);
                    window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
                    window.__vite_plugin_react_preamble_installed__=true;
                    </script><script type="module" src="/src/components/AvatarGeneration/avatar-generation.fixture.tsx"></script>
                    </body></html>`,
            })
        );
        await page.route(/\/avatar(?:\/|$)/, (route) => {
            if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
            return route.fulfill({
                json: route.request().url().includes("image-profiles")
                    ? { configured: false, profiles: [] }
                    : { avatar: null, previous_avatar: null, avatar_revision: 0, can_restore: false },
            });
        });
        await page.goto(`/avatar-role-regression.html?admin=1&roles=${roles}`);
        for (const board of [false, true]) {
            if (board) await page.getByRole("button", { name: "Toggle target", exact: true }).click();
            await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
            const dialog = page.getByRole("dialog");
            await expect(dialog).toBeVisible();
            const settings = dialog.getByRole("link");
            if (roles === "granted") await expect(settings).toBeVisible();
            else await expect(settings).toHaveCount(0);
            await dialog.getByRole("button", { name: "Close", exact: true }).click();
        }
        expect(errors).toEqual([]);
    });
}

test("semantic response is rejected after live summary profile changes", async ({ page }) => {
    let release: (() => void) | undefined;
    let sent = 0;
    await page.route("**/*", async (route) => {
        if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
        const url = new URL(route.request().url()).pathname;
        if (url.endsWith("/image-profiles"))
            return route.fulfill({ json: { profiles: [{ uid: "profile", display_name: "Images", model_name: "image-model" }] } });
        if (url.endsWith("/context"))
            return route.fulfill({
                json: {
                    external_request: false,
                    source_revision: "r1",
                    sources: [{ id: "title", kind: "project_title", text: "Reviewed board text" }],
                    profiles: [{ uid: "summary", display_name: "Summary model", model_name: "text-model" }],
                },
            });
        if (url.endsWith("/semantic-preview")) {
            sent++;
            await new Promise<void>((resolve) => {
                release = resolve;
            });
            return route
                .fulfill({ json: { external_request: true, source_revision: "r1", profile_uid: "summary", prompt: "Stale semantic output" } })
                .catch(() => undefined);
        }
        if (url.endsWith("/avatar")) return route.fulfill({ json: { avatar: null, previous_avatar: null, avatar_revision: 0, can_restore: false } });
        return route.fulfill({ json: { oidc_enabled: false } });
    });
    await page.goto(fixture);
    await page.getByRole("button", { name: "Toggle target", exact: true }).click();
    await page.getByRole("button", { name: "Generate with AI", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Prepare board summary", exact: true }).click();
    await dialog.getByRole("combobox", { name: "Avatar summary profile", exact: true }).selectOption("summary");
    await dialog.getByRole("checkbox", { name: /Send exactly the checked text sources/ }).check();
    await dialog.getByRole("button", { name: "Generate semantic icon prompt", exact: true }).click();
    await expect.poll(() => sent).toBe(1);
    await page
        .locator("nav button")
        .filter({ hasText: /^Change summary profile$/ })
        .dispatchEvent("click");
    release?.();
    await expect(dialog.getByRole("textbox", { name: "Prompt sent to the selected model", exact: true })).not.toHaveValue("Stale semantic output");
    await expect(dialog.getByRole("combobox", { name: "Avatar summary profile", exact: true })).toHaveCount(0);
    await expect(dialog.locator("img")).toHaveCount(0);
});

test("avatar summary configuration uses only native provider settings", async ({ page }) => {
    let submitted: Record<string, string> | undefined;
    await page.route("**/*", async (route) => {
        if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
        if (new URL(route.request().url()).pathname.endsWith("/settings/internal-bot") && route.request().method() === "POST") {
            const body = new Uint8Array(route.request().postDataBuffer()!);
            const fields = await new Response(body.buffer, { headers: { "content-type": route.request().headers()["content-type"] } }).formData();
            submitted = Object.fromEntries([...fields.entries()].filter((entry): entry is [string, string] => typeof entry[1] === "string"));
            return route.fulfill({ status: 201, json: { internal_bot: { uid: "created-summary" } } });
        }
        return route.fulfill({ json: { apis: {}, api_comfort_tools: [] } });
    });
    await page.setViewportSize({ width: 390, height: 1000 });
    await page.goto("/src/pages/SettingsPage/components/internalBots/provider-role.fixture.html");
    await page.getByRole("button", { name: "Open creation", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog
        .getByRole("combobox")
        .filter({ hasText: /Select a platform\*?$/ })
        .click();
    await page.getByRole("option", { name: "N8N", exact: true }).click();
    await dialog.getByRole("combobox").filter({ hasText: "Select a type" }).click();
    await page.getByRole("option", { name: "Avatar summary", exact: true }).click();
    await expect(dialog.getByRole("combobox").filter({ hasText: "Select a platform" })).toHaveCount(0);
    await expect(dialog.getByRole("combobox").filter({ hasText: "Select a running type" })).toHaveCount(0);
    await expect(dialog.getByText("Comfort tools", { exact: true })).toHaveCount(0);
    await expect(dialog.getByText("System prompt", { exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("textbox", { name: "Internal bot display name*", exact: true })).toHaveValue("");
    await dialog.getByRole("textbox", { name: "Internal bot display name*", exact: true }).fill("Reviewed avatar summary");
    await dialog.getByRole("combobox").filter({ hasText: "Select a provider" }).click();
    await page.getByRole("option", { name: "OpenAI Compatible", exact: true }).click();
    await dialog.getByRole("textbox", { name: "Base URL*", exact: true }).fill("https://summary.example.test/v1");
    await dialog.getByRole("textbox", { name: "API key*", exact: true }).fill("fixture-summary-key");
    await dialog.getByRole("textbox", { name: "Model*", exact: true }).fill("explicit-summary-model");
    await dialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect.poll(() => submitted?.bot_type).toBe("avatar_summary");
    expect(submitted?.platform).toBe("default");
    expect(submitted?.platform_running_type).toBe("default");
    const config = JSON.parse(submitted!.value);
    expect(config.agent_llm).toBe("OpenAI Compatible");
    expect(config.model_name).toBe("explicit-summary-model");
    expect(config.api_names ?? []).toEqual([]);
    await expect(dialog).toHaveCount(0);
});
