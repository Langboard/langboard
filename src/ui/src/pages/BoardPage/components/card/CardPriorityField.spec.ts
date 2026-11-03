import { expect, test } from "@playwright/test";

const path = "/src/pages/BoardPage/components/card/CardPriorityField.fixture.html";

for (const theme of ["light", "dark"]) {
    test(`urgency has four distinct rendered shapes and readable static labels in ${theme} mode`, async ({ page }) => {
        await page.goto(`${path}?signals=1${theme === "dark" ? "&dark=1" : ""}`);
        const signals = page.getByTestId("signals");
        const badges = signals.getByRole("img");
        await expect(badges).toHaveCount(4);
        const shapes: string[] = [];
        for (const label of ["Now!", "Urgent", "Normal", "Relaxed"]) {
            const badge = signals.getByRole("img", { name: `Priority: ${label}`, exact: true });
            await expect(badge.locator("svg")).toHaveCount(1);
            shapes.push(await badge.locator("svg").innerHTML());
            await expect(badge).toHaveAttribute("title", /.+/);
            const contrast = await badge.evaluate((element) => {
                const luminance = (value: string) => {
                    const rgb = value
                        .match(/[\d.]+/g)!
                        .slice(0, 3)
                        .map(Number)
                        .map((c) => {
                            const channel = c / 255;
                            return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
                        });
                    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
                };
                const text = luminance(getComputedStyle(element).color);
                const background = luminance(getComputedStyle(element.parentElement!).backgroundColor);
                return (Math.max(text, background) + 0.05) / (Math.min(text, background) + 0.05);
            });
            expect(contrast).toBeGreaterThanOrEqual(4.5);
        }
        expect(new Set(shapes).size).toBe(4);
    });
}

test("priority opens in a narrow popover, returns focus on Escape and arbitrates its card effect", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/__priority", async (route) => route.fulfill({ json: route.request().postDataJSON() }));
    await page.goto(`${path}?popover=1`);
    const trigger = page.getByRole("button", { name: /^Priority:/ });
    await expect(page.getByRole("slider")).toHaveCount(0);
    await trigger.click();
    const slider = page.getByRole("slider", { name: "Priority" });
    await expect(slider).toBeFocused();
    await slider.press("End");
    await expect(page.getByTestId("confirmed")).toHaveText("P0");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "critical");
    await expect(page.getByTestId("card")).toHaveAttribute("data-card-motion", "none");
    const box = (await page.locator(".stepped-choice-panel").boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    await slider.press("Escape");
    await expect(trigger).toBeFocused();
    await expect(trigger).toHaveAccessibleName("Priority: Now!");
    await expect(page.getByTestId("card")).toHaveAttribute("data-card-motion", "critical");
    await page.getByRole("button", { name: "Complete", exact: true }).click();
    await trigger.click();
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
});

test("release saves once, pending input is disabled and rejection restores the current confirmed value", async ({ page }) => {
    let calls = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/__priority", async (route) => {
        calls += 1;
        expect(route.request().postDataJSON()).toEqual({ priority: "P0" });
        await gate;
        await route.fulfill({ status: 403, body: "denied" });
    });
    await page.goto(path);
    const slider = page.getByRole("slider", { name: "Priority" });
    await slider.focus();
    await slider.press("End");
    await expect(slider).toBeDisabled();
    await expect.poll(() => calls).toBe(1);
    await page.getByRole("button", { name: "Socket update" }).click();
    await expect(page.getByTestId("confirmed")).toHaveText("P3");
    release();
    await expect(slider).toBeEnabled();
    await expect(slider).toHaveAttribute("aria-valuetext", "Relaxed");
    await expect(page.getByRole("alert")).toBeVisible();
    expect(calls).toBe(1);
});

test("confirmed save survives rerender and revoked permission prevents another write", async ({ page }) => {
    let calls = 0;
    await page.route("**/__priority", async (route) => {
        calls += 1;
        await route.fulfill({ json: route.request().postDataJSON() });
    });
    await page.goto(path);
    const slider = page.getByRole("slider");
    await slider.focus();
    await slider.press("End");
    await expect(page.getByTestId("confirmed")).toHaveText("P0");
    await expect(slider).toHaveAttribute("aria-valuetext", "Now!");
    await page.getByRole("button", { name: "Toggle permission" }).click();
    await expect(slider).toBeDisabled();
    await slider.press("Home");
    expect(calls).toBe(1);
    await expect(page.getByTestId("confirmed")).toHaveText("P0");
});

test("one card effect remains, offscreen and completed cards stop it", async ({ page }) => {
    await page.route("**/__priority", async (route) => route.fulfill({ json: route.request().postDataJSON() }));
    await page.goto(path);
    const card = page.getByTestId("card");
    await page.getByRole("slider").press("End");
    await expect(card).toHaveAttribute("data-card-motion", "critical");
    await expect(page.locator(".stepped-choice-panel")).toHaveAttribute("data-effect", "none");
    await page.evaluate(() => window.scrollTo(0, 1600));
    await expect(card).toHaveAttribute("data-card-motion", "none");
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(card).toHaveAttribute("data-card-motion", "critical");
    await page.getByRole("button", { name: "Complete", exact: true }).click();
    await expect(card).toHaveAttribute("data-card-motion", "none");
    await expect(page.getByTestId("confirmed")).toHaveText("P0");
});

test("reduced motion preserves urgency while disabling the enclosing card animation", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route("**/__priority", async (route) => route.fulfill({ json: route.request().postDataJSON() }));
    await page.goto(path);
    await page.getByRole("slider").press("End");
    await expect(page.getByTestId("confirmed")).toHaveText("P0");
    await expect(page.getByTestId("card")).toHaveAttribute("data-card-motion", "none");
    await expect(page.getByRole("slider")).toHaveAttribute("aria-valuetext", "Now!");
});
