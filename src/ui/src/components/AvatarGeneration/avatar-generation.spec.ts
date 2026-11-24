import { expect, test } from "@playwright/test";
const fixture = "/.verification/avatar-generation-ui-20261019/fixture.html";
const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=";
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
                        prompt: "A simple icon for 4 active workflow stages",
                        transmitted_fields: ["shared_readable_workflow_counts"],
                        external_request: false,
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
                await expect(prompt).toHaveValue("A simple icon for 4 active workflow stages");
                expect(calls.filter((call) => call.url.endsWith("/preview"))).toHaveLength(count);
                await expect(dialog.getByText(/Local aggregate summary/)).toBeVisible();
            }
            await prompt.fill("A blue simple geometric mountain icon");
            const consent = dialog.getByRole("checkbox").first();
            const generate = dialog.getByRole("button", { name: "Generate with AI", exact: true });
            await expect(generate).toBeDisabled();
            await consent.check();
            await generate.click();
            await expect(dialog.getByRole("img")).toHaveCount(6);
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
                await expect(dialog.getByRole("img")).toHaveCount(6);
                failApply = false;
                await dialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
            }
            await expect(dialog.getByRole("img")).toHaveCount(0);
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
    const consent = dialog.getByRole("checkbox").first();
    const generate = dialog.getByRole("button", { name: "Generate with AI", exact: true });
    await prompt.fill("Retain unsaved prompt");
    await consent.check();
    await generate.click();
    await expect(dialog.getByRole("img")).toHaveCount(6);
    conflict = true;
    await dialog.getByRole("button", { name: "Apply generated avatar", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("Current state was refreshed");
    await expect(prompt).toHaveValue("Retain unsaved prompt");
    await expect(dialog.getByRole("img")).toHaveCount(0);
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
        await expect(dialog.getByRole("img")).toHaveCount(0);
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
