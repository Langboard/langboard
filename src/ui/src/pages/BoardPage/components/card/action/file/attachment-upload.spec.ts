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
