import { expect, test } from "@playwright/test";

test("production settings persist without processing; explicit attachment action freezes splitter for native worker", async ({ page, request }) => {
    const bridge = process.env.LANGBOARD_RETRIEVAL_BRIDGE_URL;
    test.skip(!bridge, "Run through test_retrieval_settings_bridge.py to provision the isolated native database");
    const failures: string[] = [];
    page.on("pageerror", (error) => failures.push(error.message));
    await page.route("**/__native/**", async (route) => {
        const url = new URL(route.request().url());
        await route.fulfill({ response: await request.fetch(`${bridge}${url.pathname}`, { method: route.request().method() }) });
    });
    for (const pattern of ["**/settings/internal-bot/**", "**/board/**/document-processing"]) {
        await page.route(pattern, async (route) => {
            const url = new URL(route.request().url());
            await route.fulfill({
                response: await request.fetch(`${bridge}${url.pathname}`, {
                    method: route.request().method(),
                    headers: route.request().headers(),
                    data: route.request().postDataBuffer() ?? undefined,
                }),
            });
        });
    }
    await page.route("**/native-retrieval.html", async (route) => {
        const response = await request.get("http://127.0.0.1:4218/src/pages/SettingsPage/components/internalBots/retrieval-settings.fixture.html");
        await route.fulfill({
            contentType: "text/html",
            body: (await response.text()).replaceAll(
                "./retrieval-settings.fixture.tsx",
                "/src/pages/SettingsPage/components/internalBots/documentRetrievalNative.fixture.tsx"
            ),
        });
    });
    await page.goto("/native-retrieval.html");
    const form = page.locator("form");
    await expect(form.getByRole("spinbutton", { name: "Chunk size", exact: true })).toHaveValue("1000");
    const state = async () => (await request.get(`${bridge}/__native/state`)).json();
    expect(await state()).toMatchObject({ queued: 0, inference_calls: 0, vector_files: 0 });
    const counts: number[] = [];
    for (const size of [128, 64]) {
        await form.getByRole("spinbutton", { name: "Chunk size", exact: true }).fill(String(size));
        await form.getByRole("spinbutton", { name: "Chunk overlap", exact: true }).fill("0");
        await form.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(async () => (await state()).chunk_size).toBe(size);
        expect(await state()).toMatchObject({ queued: counts.length, inference_calls: counts.length, vector_files: counts.length ? 1 : 0 });
        await page.getByRole("button", { name: "More", exact: true }).click();
        await page.getByRole("menuitem", { name: "Index document embeddings", exact: true }).click();
        expect((await state()).queued).toBe(counts.length);
        if (!counts.length)
            await page.screenshot({ path: "../../.verification/card-work-wave-20261025/retrieval-settings/confirmation.png", fullPage: true });
        await page.getByRole("button", { name: "Index document embeddings", exact: true }).click();
        await expect.poll(async () => (await state()).queued).toBe(counts.length + 1);
        // A later real UI settings save must not alter the already frozen request.
        await form.getByRole("spinbutton", { name: "Chunk size", exact: true }).fill("256");
        await form.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(async () => (await state()).chunk_size).toBe(256);
        expect((await state()).inference_calls).toBe(counts.length);
        const processed = await (await request.post(`${bridge}/__native/work`)).json();
        expect(processed).toMatchObject({ status: "indexed", frozen_chunk_size: size, source_unchanged: true, provenance_valid: true });
        expect(processed.maximum_chunk_length).toBeLessThanOrEqual(size);
        counts.push(processed.chunk_count);
    }
    expect(counts[1]).toBeGreaterThan(counts[0]);
    expect(failures).toEqual([]);
});
