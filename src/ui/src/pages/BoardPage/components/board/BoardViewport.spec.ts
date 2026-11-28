import { expect, test } from "@playwright/test";

test("board viewport navigation stays scoped and the surviving viewport retains its identity and scroll", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/BoardViewport.fixture.html");
    const left = page.getByRole("region", { name: "Left", exact: true });
    const right = page.getByRole("region", { name: "Right", exact: true });
    const leftBar = left.getByRole("scrollbar", { name: "Scroll board horizontally" });
    const rightBar = right.getByRole("scrollbar", { name: "Scroll board horizontally" });
    await expect(leftBar).toBeVisible();
    await expect(rightBar).toBeVisible();
    const leftId = await leftBar.getAttribute("aria-controls");
    const rightId = await rightBar.getAttribute("aria-controls");
    expect(leftId).not.toBe(rightId);
    const offset = (id: string) => page.locator(`[id="${id}"]`).evaluate((node) => node.scrollLeft);
    await right.getByRole("button", { name: "Next column" }).click();
    await expect.poll(() => offset(rightId!)).toBeGreaterThan(200);
    expect(await offset(leftId!)).toBe(0);
    await left.getByRole("button", { name: "Left action" }).click();
    await expect.poll(() => offset(leftId!)).toBe(400);
    const rightOffset = await offset(rightId!);
    for (const scope of [left, right]) {
        const region = await scope.boundingBox();
        const minimap = await scope.locator("[data-board-minimap]").boundingBox();
        const dock = await scope.locator("[data-floating-nav-content]").boundingBox();
        expect(minimap!.x).toBeGreaterThanOrEqual(region!.x);
        expect(minimap!.x + minimap!.width).toBeLessThanOrEqual(region!.x + region!.width);
        expect(dock!.x).toBeGreaterThanOrEqual(region!.x);
        expect(dock!.x + dock!.width).toBeLessThanOrEqual(region!.x + region!.width);
    }
    await page.getByRole("button", { name: "Close left", exact: true }).click();
    await expect(left).toHaveCount(0);
    await expect(rightBar).toHaveAttribute("aria-controls", rightId!);
    await expect.poll(() => offset(rightId!)).toBe(rightOffset);
    await right.getByRole("button", { name: "Previous column" }).click();
    await expect.poll(() => offset(rightId!)).toBe(0);
});

test("floating navigation binds to a board that mounts after its loading state", async ({ page }) => {
    await page.goto("/src/pages/BoardPage/components/board/BoardViewport.fixture.html?deferred=1");
    const right = page.getByRole("region", { name: "Right", exact: true });
    await right.getByRole("button", { name: "Load Right", exact: true }).click();
    await expect(right.getByRole("scrollbar", { name: "Scroll board horizontally" })).toBeVisible();
    const region = await right.boundingBox();
    await expect.poll(async () => (await right.locator("[data-floating-nav-content]").boundingBox())!.x).toBeGreaterThanOrEqual(region!.x);
    await right.getByRole("button", { name: "Right action", exact: true }).click();
    const id = await right.getByRole("scrollbar", { name: "Scroll board horizontally" }).getAttribute("aria-controls");
    await expect.poll(() => page.locator(`[id="${id}"]`).evaluate((node) => node.scrollLeft)).toBe(400);
});
