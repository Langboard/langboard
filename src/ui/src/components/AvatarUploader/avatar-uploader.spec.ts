import { expect, test, type Page } from "@playwright/test";
const path = "/.verification/avatar-uploader-lifecycle-20261019/index.html";
const image = (name: string) => ({
    name,
    mimeType: "image/svg+xml",
    buffer: Buffer.from(
        `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">
            <rect width="96" height="96" fill="${name.startsWith("a") ? "red" : "green"}"/></svg>`
    ),
});
const upload = (page: Page, name: string) => page.locator("input[type=file]").setInputFiles(image(name));
const revert = (page: Page) => page.locator("button:has(svg.lucide-undo-2)").click();
const remove = (page: Page) => page.locator("svg.lucide-trash-2").click();
async function submit(page: Page) {
    await page.getByRole("button", { name: "Submit avatar" }).click();
    await expect(page.getByTestId("submitted")).not.toBeEmpty();
    return JSON.parse((await page.getByTestId("submitted").textContent())!);
}
for (const width of [390, 1440]) {
    test(`replacement, revert, deletion and cancellation at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(path);
        await expect(page.locator("img")).toBeVisible();
        const saved = await page.locator("img").getAttribute("src");
        await upload(page, "a.svg");
        await expect(page.locator("img")).not.toHaveAttribute("src", saved!);
        await revert(page);
        await expect(page.locator("img")).toHaveAttribute("src", saved!);
        expect((await submit(page)).names).toEqual([]);
        await upload(page, "a.svg");
        await upload(page, "b.svg");
        await expect(page.getByTestId("calls")).toContainText("b.svg");
        expect((await submit(page)).names).toEqual(["b.svg"]);
        const preview = await page.locator("img").getAttribute("src");
        await page.locator("input[type=file]").dispatchEvent("cancel");
        expect((await submit(page)).names).toEqual(["b.svg"]);
        await expect(page.locator("img")).toHaveAttribute("src", preview!);
        await remove(page);
        expect(await submit(page)).toMatchObject({ names: [], deleted: "true", preview: null });
        await upload(page, "a.svg");
        expect(await submit(page)).toMatchObject({ names: ["a.svg"], deleted: "false" });
        await revert(page);
        await expect(page.locator("img")).toHaveAttribute("src", saved!);
        await page.screenshot({ path: `../../.verification/avatar-uploader-lifecycle-20261019/avatar-${width}.png` });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}
test("current callbacks, disabled and validation state", async ({ page }) => {
    await page.goto(path);
    await page.getByRole("button", { name: "Update callbacks" }).click();
    await page.getByRole("button", { name: "Toggle disabled" }).click();
    await expect(page.locator("input[type=file]")).toBeDisabled();
    await page.getByRole("button", { name: "Toggle disabled" }).click();
    await upload(page, "a.svg");
    await expect(page.getByTestId("calls")).toContainText("change:1:a.svg");
    await page.getByRole("button", { name: "Toggle validating" }).click();
    await expect(page.locator("input[type=file]")).toBeDisabled();
    await remove(page);
    await expect(page.locator("img")).toBeVisible();
    await page.getByRole("button", { name: "Toggle validating" }).click();
    await remove(page);
    await expect(page.getByTestId("calls")).toContainText("delete:1");
});
test("late readers cannot overwrite replacement, revert, deletion or saved URL", async ({ page }) => {
    await page.addInitScript(() => {
        const NativeReader = window.FileReader;
        const completions: (() => void)[] = [];
        Object.assign(window, { avatarReadCompletions: completions });
        window.FileReader = class extends NativeReader {
            readAsDataURL(file: Blob) {
                const captured = this.onload;
                this.onload = (event) => completions.push(() => captured?.call(this, event));
                super.readAsDataURL(file);
            }
        };
    });
    await page.goto(path);
    const saved = await page.locator("img").getAttribute("src");
    const finish = async (index: number) => {
        await expect
            .poll(() => page.evaluate(() => (window as unknown as { avatarReadCompletions: unknown[] }).avatarReadCompletions.length))
            .toBeGreaterThan(index);
        await page.evaluate((i) => (window as unknown as { avatarReadCompletions: (() => void)[] }).avatarReadCompletions[i](), index);
    };
    await upload(page, "a.svg");
    await upload(page, "b.svg");
    await finish(1);
    const latest = await page.locator("img").getAttribute("src");
    await finish(0);
    await expect(page.locator("img")).toHaveAttribute("src", latest!);
    await upload(page, "a.svg");
    await revert(page);
    await finish(2);
    await expect(page.locator("img")).toHaveAttribute("src", saved!);
    expect((await submit(page)).names).toEqual([]);
    await upload(page, "b.svg");
    await remove(page);
    await finish(3);
    await expect(page.locator("img")).toHaveCount(0);
    await upload(page, "a.svg");
    await page.getByRole("button", { name: "Update saved avatar" }).click();
    await finish(4);
    await expect(page.locator("img")).toHaveAttribute("src", saved + "#saved");
    expect((await submit(page)).names).toEqual([]);
});
