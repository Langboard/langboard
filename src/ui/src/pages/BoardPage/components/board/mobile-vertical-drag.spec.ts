import { expect, test, type Page, type CDPSession } from "@playwright/test";
const sessions = new WeakMap<Page, CDPSession>();
const card = (page: Page, id = 0) => page.locator(`#mobile-board [data-board-card-touch-dnd-uid="mobile-card-${id}"]`);
const owner = (page: Page, column = "source") =>
    page.locator(`[data-board-column-touch-dnd-uid=${column}] [data-radix-scroll-area-viewport]`).first();
async function touch(page: Page, type: "touchStart" | "touchMove" | "touchEnd" | "touchCancel", x = 0, y = 0, extra = false) {
    let session = sessions.get(page);
    if (!session) {
        session = await page.context().newCDPSession(page);
        sessions.set(page, session);
    }
    await session.send("Input.dispatchTouchEvent", {
        type,
        touchPoints: ["touchEnd", "touchCancel"].includes(type) ? [] : [{ x, y, id: 1 }, ...(extra ? [{ x: x + 20, y: y + 20, id: 2 }] : [])],
    });
}
async function open(page: Page, query = "") {
    await page.route("**/vertical-drag.html*", (route) =>
        route.fulfill({
            contentType: "text/html",
            body: `<!doctype html><html>
        <head><meta name="viewport" content="width=device-width,initial-scale=1"/></head><body><div id="root"></div>
        <script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;</script>
        <script type="module" src="/src/pages/BoardPage/components/board/mobile-vertical-drag.fixture.tsx"></script></body></html>`,
        })
    );
    await page.goto(`/vertical-drag.html${query}`);
    await expect(card(page)).toBeVisible();
}
async function start(page: Page) {
    const box = await card(page).locator("[data-board-card-touch-handle]").boundingBox();
    await touch(page, "touchStart", box!.x + box!.width / 2, box!.y + box!.height / 2);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "dragging");
}
async function receipt(page: Page) {
    return JSON.parse(await page.locator("#receipts").innerText());
}
for (const width of [390, 320])
    test(`native virtualized column reaches an initially invisible lower drop target at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await open(page);
        const viewport = owner(page);
        const rect = await viewport.boundingBox();
        await expect(card(page, 25)).toHaveCount(0);
        await start(page);
        await touch(page, "touchMove", 120, rect!.y + rect!.height - 3);
        await expect.poll(() => viewport.evaluate((element) => element.scrollTop), { timeout: 12000 }).toBeGreaterThan(1400);
        await expect(card(page)).toHaveCount(0);
        await touch(page, "touchMove", 120, rect!.y + rect!.height / 2);
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
        // Stop at a visible lower target using fresh geometry after virtualization and scroll.
        const target = await page
            .locator("#mobile-board [data-board-column-touch-dnd-uid=source] [data-board-card-touch-dnd-uid]")
            .evaluateAll((nodes) => {
                const view = document
                    .querySelector("[data-board-column-touch-dnd-uid=source] [data-radix-scroll-area-viewport]")!
                    .getBoundingClientRect();
                return nodes
                    .map((node) => ({ uid: node.getAttribute("data-board-card-touch-dnd-uid"), rect: node.getBoundingClientRect() }))
                    .filter(({ rect }) => rect.top > view.top + 72 && rect.bottom < view.bottom - 72)
                    .at(-1);
            });
        expect(target).toBeTruthy();
        const index = Number(target!.uid!.split("-").at(-1));
        expect(index).toBeGreaterThan(8);
        await touch(page, "touchMove", 120, target!.rect.y + target!.rect.height - 4);
        await touch(page, "touchEnd");
        expect(await receipt(page)).toContainEqual(expect.objectContaining({ rowUID: "mobile-card-0", order: index }));
        const after = await viewport.evaluate((element) => element.scrollTop);
        await page.waitForTimeout(150);
        expect(await viewport.evaluate((element) => element.scrollTop)).toBe(after);
        await page.screenshot({ path: `.verification/mobile-vertical-drag-${width}.png` });
    });
test("both axes move together and cancel stops scrolling", async ({ page }) => {
    await open(page);
    await start(page);
    const rect = await owner(page).boundingBox();
    await touch(page, "touchMove", 384, rect!.y + rect!.height - 3);
    await expect.poll(() => page.locator("#mobile-board").evaluate((element) => element.scrollLeft)).toBeGreaterThan(100);
    await expect.poll(() => owner(page, "destination").evaluate((element) => element.scrollTop)).toBeGreaterThan(30);
    await touch(page, "touchCancel");
    const stopped = await page
        .locator("#mobile-board")
        .evaluate((element) => [
            element.scrollLeft,
            ...Array.from(element.querySelectorAll("[data-radix-scroll-area-viewport]"), (node) => node.scrollTop),
        ]);
    await page.waitForTimeout(150);
    expect(
        await page
            .locator("#mobile-board")
            .evaluate((element) => [
                element.scrollLeft,
                ...Array.from(element.querySelectorAll("[data-radix-scroll-area-viewport]"), (node) => node.scrollTop),
            ])
    ).toEqual(stopped);
    expect(await receipt(page)).toEqual([]);
});
test("ordinary body swipe keeps native vertical scroll and unmount stops an active drag", async ({ page }) => {
    await open(page);
    const rect = await owner(page).boundingBox();
    await touch(page, "touchStart", 100, rect!.y + rect!.height - 20);
    for (let y = rect!.y + rect!.height - 40; y > rect!.y + 40; y -= 25) await touch(page, "touchMove", 100, y);
    await touch(page, "touchEnd");
    await expect.poll(() => owner(page).evaluate((element) => element.scrollTop)).toBeGreaterThan(20);
    expect(await receipt(page)).toEqual([]);
    // Let native inertial scrolling settle before beginning a separate held-handle gesture.
    await page.waitForTimeout(250);
    await owner(page).evaluate((element) => {
        element.scrollTop = 0;
    });
    await card(page).scrollIntoViewIfNeeded();
    await start(page);
    await touch(page, "touchMove", 100, rect!.y + rect!.height - 3);
    await page.getByRole("button", { name: "Unmount board", exact: true }).evaluate((button) => (button as HTMLButtonElement).click());
    await expect(page.locator("#mobile-board")).toHaveCount(0);
    await touch(page, "touchCancel");
    expect(await receipt(page)).toEqual([]);
});

test("a second touch stops both axes without a drop", async ({ page }) => {
    await open(page);
    await start(page);
    const rect = await owner(page).boundingBox();
    await touch(page, "touchMove", 384, rect!.y + rect!.height - 3);
    await expect.poll(() => page.locator("#mobile-board").evaluate((element) => element.scrollLeft)).toBeGreaterThan(40);
    await touch(page, "touchStart", 100, rect!.y + rect!.height - 40, true);
    const positions = () =>
        page
            .locator("#mobile-board")
            .evaluate((element) => [
                element.scrollLeft,
                ...Array.from(element.querySelectorAll("[data-radix-scroll-area-viewport]"), (node) => node.scrollTop),
            ]);
    const stopped = await positions();
    await page.waitForTimeout(150);
    expect(await positions()).toEqual(stopped);
    await expect(page.locator("body > [data-board-card-touch-dnd-uid]")).toHaveCount(0);
    await touch(page, "touchCancel");
    expect(await receipt(page)).toEqual([]);
});

test("automatic columns reject local mouse reorder and accept a drop into an empty column", async ({ browser }) => {
    const context = await browser.newContext({ hasTouch: false, viewport: { width: 1500, height: 900 } });
    const page = await context.newPage();
    await open(page, "?sort=created_newest");
    const source = card(page).locator("[draggable=true]");
    await source.dragTo(card(page, 1));
    expect(await receipt(page)).toEqual([]);
    const empty = page.locator("[data-board-column-touch-dnd-uid=automatic]");
    await source.dragTo(empty, { targetPosition: { x: 110, y: 160 } });
    expect(await receipt(page)).toContainEqual(expect.objectContaining({ rowUID: "mobile-card-0", parentUID: "automatic", order: 0 }));
    await context.close();
});
