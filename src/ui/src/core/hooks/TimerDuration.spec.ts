import { expect, test } from "@playwright/test";
const fixture = "/src/core/hooks/TimerDuration.fixture.html";
test("100 active worker avatars tick only while inspected and release timers on pause or unmount", async ({ page }) => {
    await page.clock.install();
    await page.addInitScript(() => {
        const intervals = new Set<number>();
        const interval = window.setInterval.bind(window);
        const clear = window.clearInterval.bind(window);
        window.setInterval = ((...args: Parameters<typeof window.setInterval>) => {
            const id = interval(...args);
            if (args[1] === 1000) intervals.add(id);
            return id;
        }) as typeof window.setInterval;
        window.clearInterval = (id) => {
            intervals.delete(Number(id));
            clear(id);
        };
        window.pendingTimerCount = () => intervals.size;
    });
    await page.goto(`${fixture}?workers`);
    const avatars = page.getByRole("region", { name: "Worker avatars" }).locator("[tabindex='0']");
    await expect(avatars).toHaveCount(100);
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
    await page.clock.runFor(5000);
    await avatars.first().hover();
    await expect(avatars.first()).toHaveAttribute("title", "Working: Task · 5s");
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(1);
    await page.clock.runFor(2000);
    await expect(avatars.first()).toHaveAttribute("title", "Working: Task · 7s");
    await avatars.first().focus();
    await page.mouse.move(1200, 700);
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(1);
    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
    await page.evaluate(() => window.workerFixture.sample(120));
    await avatars.last().focus();
    await expect(avatars.last()).toHaveAttribute("title", "Working: Task · 2m");
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(1);
    await page.evaluate(() => {
        Object.defineProperty(document, "hidden", { configurable: true, value: true });
        document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
    await page.clock.runFor(3000);
    await page.evaluate(() => {
        Reflect.deleteProperty(document, "hidden");
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(avatars.last()).toHaveAttribute("title", "Working: Task · 2m 3s");
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(1);
    await page.evaluate(() => {
        window.workerFixture.sample(123);
        window.workerFixture.pause();
    });
    await expect(avatars.last()).toHaveAttribute("title", "Paused: Task · 2m 3s");
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
    await page.evaluate(() => window.workerFixture.resume());
    await expect(avatars.last()).toHaveAttribute("title", "Working: Task · 2m 3s");
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(1);
    await page.evaluate(() => window.workerFixture.unmount());
    await expect(avatars).toHaveCount(0);
    expect(await page.evaluate(() => window.pendingTimerCount())).toBe(0);
});
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
