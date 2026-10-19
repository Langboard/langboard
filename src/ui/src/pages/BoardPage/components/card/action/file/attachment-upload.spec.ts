import { expect, test } from "@playwright/test";
const path = "/src/pages/BoardPage/components/card/action/file/attachment-upload.fixture.html";
for (const width of [1440, 390]) {
    test(`failed upload remains removable and does not invent a size cause at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        let calls = 0;
        await page.route("**/board/fixture/card/fixture-card/attachment", async (route) => {
            calls++;
            await route.abort("accessdenied");
        });
        page.on("pageerror", (error) => console.log("fixture page error:", error.message));
        await page.goto(path);
        await page.getByRole("button", { name: "Attach file", exact: true }).click();
        const chooser = page.waitForEvent("filechooser");
        await page.getByRole("button", { name: "Upload a file", exact: true }).click();
        await (await chooser).setFiles({ name: "small.txt", mimeType: "text/plain", buffer: Buffer.from("small upload") });
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByText("1 files could not be uploaded.", { exact: true }).first()).toBeVisible();
        await expect(page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Delete", exact: true }).click();
        await expect(page.getByText("small.txt", { exact: true })).toHaveCount(0);
        expect(calls).toBe(1);
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

for (const width of [1440, 390]) {
    for (const outcome of ["queued", "failed"]) {
        test(`explicit keyword action confirms current attachment and handles ${outcome} at ${width}px`, async ({ page }, testInfo) => {
            await page.setViewportSize({ width, height: 850 });
            let release!: () => void;
            const gate = new Promise<void>((resolve) => {
                release = resolve;
            });
            const bodies: unknown[] = [];
            await page.route("**/board/fixture/card/fixture-card/attachment/fixture-attachment/document-processing", async (route) => {
                bodies.push(route.request().postDataJSON());
                await gate;
                await route.fulfill({
                    status: outcome === "queued" ? 200 : 400,
                    contentType: "application/json",
                    body: JSON.stringify(outcome === "queued" ? { status: "pending" } : { code: "VA0000" }),
                });
            });
            await page.goto(path);
            await page.getByRole("button", { name: "More", exact: true }).click();
            await page.getByRole("menuitem", { name: "Reindex document keywords", exact: true }).click();
            await expect(
                page.getByText(
                    "Index only the current complete text of this attachment for keyword search. Transcription and embeddings are not rerun.",
                    { exact: true }
                )
            ).toBeVisible();
            expect(bodies).toHaveLength(0);
            if (outcome === "queued")
                await page.screenshot({
                    path: testInfo.outputPath("confirmation.png"),
                    fullPage: true,
                    animations: "disabled",
                });
            const confirm = page.getByRole("button", { name: "Reindex document keywords", exact: true });
            await confirm.click();
            await expect(confirm).toBeDisabled();
            await expect.poll(() => bodies.length).toBe(1);
            expect(bodies).toEqual([{ mode: "keyword" }]);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            if (outcome === "queued")
                await page.screenshot({
                    path: testInfo.outputPath("pending.png"),
                    fullPage: true,
                    animations: "disabled",
                });
            release();
            if (outcome === "queued") {
                await expect(page.getByText("Keyword indexing queued", { exact: true })).toBeVisible();
            } else {
                await expect(page.getByText("Keyword indexing queued", { exact: true })).toHaveCount(0);
                await expect(page.locator("[data-sonner-toast][data-type=error]")).toBeVisible();
            }
            await expect(confirm).toHaveCount(0);
            if (outcome === "failed") {
                await page.screenshot({
                    path: testInfo.outputPath("failure.png"),
                    fullPage: true,
                    animations: "disabled",
                });
            }
        });
    }
}
