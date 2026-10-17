import { test, expect } from "@playwright/test";

const fixture = "/src/pages/BoardPage/components/card/checklist/BoardCardCheckitemTimer.fixture.html";

test("timer immediately displays the authoritative paused duration", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-10-16T10:00:00Z") });
    await page.goto(fixture);
    const timer = page.getByRole("button", { name: "Manage timer", exact: true });
    await expect(timer).toContainText("10");
    await page.clock.runFor(2000);
    await expect(timer).toContainText("12");
    await page.getByRole("button", { name: "Receive paused snapshot" }).click();
    await expect(timer).toContainText("27");
    await page.clock.runFor(3000);
    await expect(timer).toContainText("27");
});

test("a corrected start timestamp updates a running timer without a status transition", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-10-16T10:00:00Z") });
    await page.goto(fixture);
    const timer = page.getByRole("button", { name: "Manage timer", exact: true });
    await expect(timer).toContainText("10");
    await page.getByRole("button", { name: "Receive corrected start" }).click();
    await expect(timer).toContainText("15");
    await page.clock.runFor(2000);
    await expect(timer).toContainText("17");
});
