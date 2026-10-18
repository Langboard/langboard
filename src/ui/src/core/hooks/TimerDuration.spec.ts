import { expect, test } from "@playwright/test";
const fixture = "/src/core/hooks/TimerDuration.fixture.html";
test("stopped timer immediately reflects accumulated time in tracking and detail", async ({ page }) => {
    await page.goto(fixture);
    await expect(page.getByTestId("tracking")).toContainText("0s");
    await page.evaluate(() => window.timerFixture.accumulated(120));
    await expect(page.getByTestId("tracking")).toContainText("2m");
    await expect(page.getByTestId("detail")).toContainText("2m");
});
test("timer start-time corrections update both views and pause stops ticking", async ({ page }) => {
    await page.clock.install();
    await page.goto(fixture);
    await expect(page.getByTestId("tracking")).toContainText("0s");
    await page.evaluate(() => window.timerFixture.start(30));
    await expect(page.getByTestId("tracking")).toContainText("30s");
    await page.evaluate(() => window.timerFixture.start(90));
    await expect(page.getByTestId("tracking")).toContainText("1m 30s");
    await page.clock.runFor(2000);
    await expect(page.getByTestId("tracking")).toContainText("1m 32s");
    await page.evaluate(() => {
        window.timerFixture.accumulated(92);
        window.timerFixture.pause();
    });
    await page.clock.runFor(3000);
    await expect(page.getByTestId("tracking")).toContainText("1m 32s");
    await expect(page.getByTestId("detail")).toContainText("1m 32s");
});

test("stopped and paused views leave no recurring timer work", async ({ page }) => {
    await page.clock.install();
    await page.goto(fixture);
    await expect(page.getByTestId("tracking")).toContainText("0s");
    await page.evaluate(() => {
        const timeouts = new Set<number>();
        const intervals = new Set<number>();
        const timeout = window.setTimeout.bind(window);
        const interval = window.setInterval.bind(window);
        const clearTimeout = window.clearTimeout.bind(window);
        const clearInterval = window.clearInterval.bind(window);
        window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
            if (typeof handler !== "function") return timeout(handler, delay, ...args);
            const id = timeout(() => {
                timeouts.delete(id);
                handler(...args);
            }, delay);
            timeouts.add(id);
            return id;
        }) as typeof window.setTimeout;
        window.setInterval = ((...args: Parameters<typeof window.setInterval>) => {
            const id = interval(...args);
            intervals.add(id);
            return id;
        }) as typeof window.setInterval;
        window.clearTimeout = (id) => {
            timeouts.delete(Number(id));
            clearTimeout(id);
        };
        window.clearInterval = (id) => {
            intervals.delete(Number(id));
            clearInterval(id);
        };
        Object.assign(window, { pendingTimerCount: () => timeouts.size + intervals.size });
    });
    await page.clock.runFor(3000);
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
    await page.evaluate(() => window.timerFixture.start(30));
    await expect(page.getByTestId("tracking")).toContainText("30s");
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(2);
    await page.evaluate(() => {
        window.timerFixture.accumulated(30);
        window.timerFixture.pause();
    });
    await page.clock.runFor(3000);
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
});
