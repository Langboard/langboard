import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const fixture = "/src/components/VideoPlayer/video-player.fixture.html";
const video = readFileSync(new URL("./fixtures/fixture.mp4", import.meta.url));
const sprite = readFileSync(new URL("./fixtures/storyboard.jpg", import.meta.url));
const vtt = readFileSync(new URL("./fixtures/storyboard.vtt", import.meta.url), "utf8");

for (const surface of ["direct", "markdown", "plate", "preview"]) {
    test(`${surface}: authenticated attachment plays, seeks and loads native hover previews`, async ({ page }, testInfo) => {
        const authenticated: string[] = [];
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.stack ?? error.message));
        await page.route("**/file/qa/card_attachment/fixture.mp4", async (route) => {
            if (route.request().method() === "OPTIONS") {
                await route.fulfill({
                    status: 204,
                    headers: {
                        "Access-Control-Allow-Origin": "http://127.0.0.1:4187",
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Allow-Headers": "authorization",
                    },
                });
                return;
            }
            authenticated.push(route.request().headers().authorization ?? "");
            await route.fulfill({
                contentType: "video/mp4",
                body: video,
                headers: {
                    "Access-Control-Allow-Origin": "http://127.0.0.1:4187",
                    "Access-Control-Allow-Credentials": "true",
                },
            });
        });
        const previews: string[] = [];
        await page.route("**/board/fixture-project/card/fixture-card/attachment/fixture-attachment/storyboard", async (route) => {
            const headers = {
                "Access-Control-Allow-Origin": "http://127.0.0.1:4187",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": "authorization",
            };
            if (route.request().method() === "OPTIONS") {
                await route.fulfill({ status: 204, headers });
                return;
            }
            previews.push(route.request().headers().authorization ?? "");
            await route.fulfill({ json: { available: true, vtt, sprite_base64: sprite.toString("base64") }, headers });
        });
        await page.goto(`${fixture}?surface=${surface}`);
        const media = page.locator("video");
        await expect(media).toBeAttached();
        expect(errors).toEqual([]);
        await expect.poll(() => authenticated.length).toBe(1);
        await expect.poll(() => media.evaluate((node: HTMLVideoElement) => node.readyState)).toBeGreaterThanOrEqual(2);
        await expect.poll(() => media.evaluate((node: HTMLVideoElement) => node.getBoundingClientRect().width)).toBeGreaterThan(200);
        expect(authenticated).toEqual(["Bearer fixture-session"]);
        expect(await media.evaluate((node: HTMLVideoElement) => node.controls)).toBe(false);
        await page.getByRole("button", { name: "재생", exact: true }).first().click();
        await expect.poll(() => media.evaluate((node: HTMLVideoElement) => node.currentTime)).toBeGreaterThan(0.1);
        await page.getByRole("button", { name: "일시중지", exact: true }).first().click();
        const slider = page.getByRole("slider", { name: "탐색", exact: true });
        await slider.focus();
        if (surface === "preview") {
            await expect(page.locator('input[type="number"]')).toHaveCount(1);
            await slider.press("ArrowRight");
            await expect(media).toHaveCount(1);
            await expect(media).toHaveAttribute("src", /^blob:/);
        }
        await slider.press("End");
        await expect.poll(() => media.evaluate((node: HTMLVideoElement) => node.currentTime)).toBeGreaterThan(2);
        await expect.poll(() => previews.length).toBe(1);
        expect(previews).toEqual(["Bearer fixture-session"]);
        await slider.locator("..").hover();
        const thumbnail = page.locator('img[src^="blob:"]');
        await expect(thumbnail).toBeVisible();
        expect(await thumbnail.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(640);
        await expect.poll(() => thumbnail.locator("..").evaluate((node) => Number(getComputedStyle(node).opacity))).toBeGreaterThan(0.99);
        await page.screenshot({ path: testInfo.outputPath("oss-hover-storyboard.png") });
        if (surface === "preview") {
            await slider.press("Escape");
            await expect(media).toHaveCount(0);
        }
    });
}

test("failed protected media is not retried through an unauthenticated video URL", async ({ page }) => {
    let calls = 0;
    await page.route("**/file/qa/card_attachment/fixture.mp4", async (route) => {
        if (route.request().method() === "OPTIONS") {
            await route.fulfill({
                status: 204,
                headers: {
                    "Access-Control-Allow-Origin": "http://127.0.0.1:4187",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Headers": "authorization",
                },
            });
            return;
        }
        calls += 1;
        await route.fulfill({
            status: 404,
            contentType: "application/json",
            body: "{}",
            headers: {
                "Access-Control-Allow-Origin": "http://127.0.0.1:4187",
                "Access-Control-Allow-Credentials": "true",
            },
        });
    });
    await page.goto(fixture);
    await expect.poll(() => calls).toBe(1);
    await expect(page.locator("video")).not.toHaveAttribute("src", /file\//);
    await page.getByRole("button", { name: "External", exact: true }).click();
    await expect(page.locator("video")).toHaveAttribute("src", "https://external.invalid/fixture.mp4");
});
