import { expect, test } from "@playwright/test";
test.beforeEach(async ({ page }) => {
    await page.route("**/schema/api**", (route) => route.fulfill({ json: { apis: {}, api_comfort_tools: [] } }));
    page.on("pageerror", (error) => console.log("Image UI error:", error.message));
    page.on("console", (message) => {
        if (message.type() === "error") console.log("Image console:", message.text());
    });
    page.on("requestfailed", (request) => console.log("Image request failed", request.url(), request.failure()));
});
const fixture = "/.verification/image-generation-ui-20261019/fixture.html";
const preview = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=";
for (const width of [390, 1440]) {
    test(`explicit image profile save, catalog and consent at ${width}`, async ({ page }, testInfo) => {
        const requests: { url: string; method: string; body: string }[] = [];
        let saveFails = true;
        let generationStatus = 200;
        await page.route("**/settings/**", async (route) => {
            if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
            const request = route.request();
            requests.push({ url: request.url(), method: request.method(), body: request.postData() ?? "" });
            if (request.url().endsWith("/models")) return route.fulfill({ json: { models: ["image-model", "second-model"] } });
            if (request.url().endsWith("/preview"))
                return route.fulfill({
                    status: generationStatus,
                    json: generationStatus === 200 ? { preview_data_url: preview } : { detail: "provider-secret-must-not-display" },
                });
            return route.fulfill({ status: saveFails ? 500 : 200, json: saveFails ? { detail: "provider-secret-must-not-display" } : {} });
        });
        await page.setViewportSize({ width, height: 1000 });
        await page.goto(fixture);
        const section = page.getByRole("region", { name: "Image generation", exact: true });
        await expect(section).toBeVisible();
        expect(requests).toHaveLength(0);
        const key = section.getByLabel("Internal bot API key", { exact: true });
        const model = section.getByLabel("Model", { exact: true });
        const fetch = section.getByRole("button", { name: "Fetch models", exact: true });
        const generate = section.getByRole("button", { name: "Generate test image", exact: true });
        await expect(key).toHaveValue("");
        await expect(section.getByLabel("Image size", { exact: true })).toHaveAttribute("readonly", "");
        await fetch.click();
        await expect(section.getByRole("combobox").filter({ hasText: "Available models" })).toBeVisible();
        expect(requests).toHaveLength(1);
        expect(requests[0].method).toBe("GET");
        expect(requests[0].url).toContain("/image-generation/image-profile/models");
        await key.fill("fixture-write-only-key");
        await model.fill("edited-model");
        await expect(fetch).toBeDisabled();
        await section.getByRole("button", { name: "Save", exact: true }).click();
        await expect(section.getByRole("alert")).toContainText("Could not save");
        await expect(key).toHaveValue("fixture-write-only-key");
        await expect(model).toHaveValue("edited-model");
        await expect(page.locator("body")).not.toContainText("provider-secret-must-not-display");
        const write = requests.find((request) => request.method === "PUT")!;
        expect(write.body).toContain("fixture-write-only-key");
        expect(write.body).toMatch(/name="api_key"/);
        expect(write.body).toMatch(/name="value"/);
        expect(write.body).not.toMatch(/"api_key":/);
        await section.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(key).toHaveValue("");
        await expect(model).toHaveValue("image-model");
        saveFails = false;
        await key.fill("fixture-write-only-key");
        await section.getByRole("button", { name: "Save", exact: true }).click();
        await expect(key).toHaveValue("");
        await expect(generate).toBeDisabled();
        const prompt = section.getByRole("textbox", { name: "Describe a test icon", exact: true });
        await prompt.fill("A simple blue mountain icon");
        await expect(generate).toBeDisabled();
        const consent = section.getByRole("checkbox");
        await consent.check();
        await generate.click();
        await expect(section.getByRole("img", { name: "Generated preview", exact: true })).toHaveCount(3);
        await expect(consent).not.toBeChecked();
        await expect(generate).toBeDisabled();
        expect(
            await section
                .getByRole("img", { name: "Generated preview", exact: true })
                .evaluateAll((images) => images.map((image) => image.getAttribute("width")))
        ).toEqual(["32", "48", "96"]);
        await expect(page.getByTestId("avatar")).toHaveText("Original avatar unchanged");
        await page.screenshot({ path: testInfo.outputPath(`image-${width}-preview.png`), fullPage: true });
        generationStatus = 429;
        await consent.check();
        await generate.click();
        await expect(section.getByRole("alert")).toContainText("quota or rate limit");
        await expect(page.locator("body")).not.toContainText("provider-secret-must-not-display");
        expect(requests.filter((request) => request.url.endsWith("/preview"))).toHaveLength(2);
        await page.getByRole("button", { name: "Disable profile", exact: true }).click();
        await consent.check();
        await expect(generate).toBeDisabled();
        expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain(
            "fixture-write-only-key"
        );
    });
}

test("catalog and generation discard rotated identity, edits, permission and cancellation", async ({ page }) => {
    let release: (() => void) | undefined;
    let lastURL = "";
    const seen: string[] = [];
    await page.route("**/settings/**", async (route) => {
        if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
        lastURL = route.request().url();
        seen.push(lastURL);
        await new Promise<void>((resolve) => {
            release = resolve;
        });
        await route
            .fulfill({ json: lastURL.endsWith("/models") ? { models: ["stale-model"] } : { preview_data_url: preview } })
            .catch(() => undefined);
    });
    await page.goto(fixture);
    const section = page.getByRole("region", { name: "Image generation", exact: true });
    await section.getByRole("button", { name: "Fetch models", exact: true }).click();
    await expect.poll(() => seen.length).toBe(1);
    await section.getByLabel("Base URL", { exact: true }).fill("https://changed.example/v1");
    release?.();
    await expect(section.getByRole("combobox").filter({ hasText: "Available models" })).toHaveCount(0);
    await section.getByRole("button", { name: "Cancel", exact: true }).click();
    const prompt = section.getByRole("textbox", { name: "Describe a test icon", exact: true });
    const consent = section.getByRole("checkbox");
    const generate = section.getByRole("button", { name: "Generate test image", exact: true });
    for (const action of ["cancel", "Rotate model", "Toggle permission", "Switch actor"]) {
        await prompt.fill("Test icon");
        await consent.check();
        const before = seen.length;
        await generate.click();
        await expect.poll(() => seen.length).toBe(before + 1);
        if (action === "cancel") await section.getByRole("button", { name: "Cancel", exact: true }).last().click();
        else await page.getByRole("button", { name: action, exact: true }).click();
        release?.();
        await expect(section.getByRole("img", { name: "Generated preview", exact: true })).toHaveCount(0);
        await expect(consent).not.toBeChecked();
        if (action === "Toggle permission") await page.getByRole("button", { name: action, exact: true }).click();
    }
    await expect(prompt).toHaveValue("");
});

test("creation image branch preserves failed edits and clears secrets on close", async ({ page }) => {
    const requests: string[] = [];
    await page.route("**/settings/**", async (route) => {
        if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.continue();
        requests.push(route.request().postData() ?? "");
        await route.fulfill({ status: 500, json: { detail: "redacted-fixture-key" } });
    });
    await page.goto(fixture);
    await page.getByRole("button", { name: "Open creation", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox").filter({ hasText: "Select a type" }).click();
    await page.getByRole("option", { name: "Image generation", exact: true }).click();
    await expect(dialog.getByLabel("Base URL", { exact: true })).toBeVisible();
    await dialog.getByLabel(/Internal bot display name/).fill("New image profile");
    await dialog.getByLabel("Model", { exact: true }).fill("new-image-model");
    await dialog.getByLabel("Internal bot API key", { exact: true }).fill("new-profile-key");
    await dialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/Could not save/)).toBeVisible();
    await expect(dialog.getByLabel("Internal bot API key", { exact: true })).toHaveValue("new-profile-key");
    expect(requests.at(-1)).toContain("image_generation");
    expect(requests.at(-1)).toContain("new-profile-key");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Open creation", exact: true }).click();
    await expect(dialog.getByLabel("Internal bot API key", { exact: true })).toHaveValue("");
});

test("late save cannot hydrate a changed actor or rotated profile", async ({ page }) => {
    for (const change of ["Switch actor", "Rotate model"]) {
        let release: (() => void) | undefined;
        let started = false;
        await page.route("**/settings/internal-bot/image-profile", async (route) => {
            if (route.request().method() !== "PUT") return route.continue();
            started = true;
            await new Promise<void>((resolve) => {
                release = resolve;
            });
            await route.fulfill({
                json: {
                    internal_bot: {
                        uid: "image-profile",
                        bot_type: "image_generation",
                        value: JSON.stringify({
                            agent_llm: "OpenAI",
                            base_url: "https://api.openai.com/v1",
                            enabled: true,
                            size: "1024x1024",
                            model_name: "stale-response-model",
                        }),
                        api_key: "server-key-must-not-return",
                        created_at: "2026-10-19T00:00:00Z",
                        updated_at: "2026-10-19T03:00:00Z",
                    },
                },
            });
        });
        await page.goto(fixture);
        const section = page.getByRole("region", { name: "Image generation", exact: true });
        await section.getByLabel("Model", { exact: true }).fill("legitimate-edit");
        await section.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => started).toBe(true);
        await page.getByRole("button", { name: change, exact: true }).click();
        release?.();
        await expect(section.getByLabel("Model", { exact: true })).toHaveValue(change === "Rotate model" ? "rotated-model" : "image-model");
        await expect(section.getByLabel("Internal bot API key", { exact: true })).toHaveValue("");
        await expect(page.locator("body")).not.toContainText("stale-response-model");
        await page.unroute("**/settings/internal-bot/image-profile");
    }
});
