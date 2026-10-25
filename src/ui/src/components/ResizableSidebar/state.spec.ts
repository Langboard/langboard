import { expect, test } from "@playwright/test";

const fixture = "/src/components/ResizableSidebar/state.fixture.html";
const offset = (page: import("@playwright/test").Page) => page.locator("main").evaluate((node) => getComputedStyle(node).marginLeft);

test("collapsed sidebar restores its overlay offset after hide and reveal", async ({ page }) => {
    await page.goto(fixture);
    const sidebar = page.locator("[data-workbench-sidebar]");
    await sidebar.getByRole("button", { name: /Collapse|접기/ }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect.poll(() => offset(page)).toBe("28px");
    await page.getByRole("button", { name: "Toggle visibility" }).click();
    await expect.poll(() => offset(page)).toBe("0px");
    await page.getByRole("button", { name: "Toggle visibility" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect.poll(() => offset(page)).toBe("28px");
});

test("owner collapse state updates native sidebar and overlay together", async ({ page }) => {
    await page.goto(fixture);
    const sidebar = page.locator("[data-workbench-sidebar]");
    await page.getByRole("button", { name: "Toggle owner state" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect.poll(() => offset(page)).toBe("28px");
    await page.getByRole("button", { name: "Toggle owner state" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect.poll(() => offset(page)).toBe("280px");
});

test("resized expanded width remains aligned after hide and reveal", async ({ page }) => {
    await page.goto(fixture);
    const sidebar = page.locator("[data-workbench-sidebar]");
    const handle = (await sidebar.locator(".resizer").boundingBox())!;
    await page.mouse.move(handle.x + 1, handle.y + 100);
    await page.mouse.down();
    await page.mouse.move(handle.x + 81, handle.y + 100, { steps: 4 });
    await page.mouse.up();
    await expect.poll(() => offset(page)).toBe("360px");
    await page.getByRole("button", { name: "Toggle visibility" }).click();
    await expect.poll(() => offset(page)).toBe("0px");
    await page.getByRole("button", { name: "Toggle visibility" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect.poll(() => sidebar.evaluate((node) => getComputedStyle(node).maxWidth)).toBe("360px");
    await expect.poll(() => offset(page)).toBe("360px");
});
