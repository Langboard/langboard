import { expect, test, type Page, type CDPSession } from "@playwright/test";
const card = (page: Page, id = 0) => page.locator(`#mobile-board [data-board-card-touch-dnd-uid="mobile-card-${id}"]`);
const handle = (page: Page, id = 0) => card(page, id).locator("[data-board-card-touch-handle]");
async function open(page: Page, query = "") {
    await page.route("**/mobile-drag.html*", (route) =>
        route.fulfill({
            contentType: "text/html",
            body: `<html>
<head><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body><div id="root"></div>
<script type="module">
import RefreshRuntime from "/@react-refresh";
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
window.__vite_plugin_react_preamble_installed__=true;
</script>
<script type="module" src="/src/pages/BoardPage/components/board/mobile-drag.fixture.tsx"></script></body></html>`,
        })
    );
    await page.goto(`/mobile-drag.html${query}`);
    await expect(card(page)).toBeVisible();
}
async function receipts(page: Page) {
    return JSON.parse(await page.locator("#receipts").innerText());
}
async function point(locator: ReturnType<typeof handle>) {
    const box = await locator.boundingBox();
    expect(box).toBeTruthy();
    return { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 };
}
const sessions = new WeakMap<Page, CDPSession>();
async function touch(page: Page, type: "touchStart" | "touchEnd" | "touchMove" | "touchCancel", x: number, y: number) {
    let session = sessions.get(page);
    if (!session) {
        session = await page.context().newCDPSession(page);
        sessions.set(page, session);
    }
    await session.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" || type === "touchCancel" ? [] : [{ x, y }] });
}
test("native handle is discoverable with a 44px target and clear labels/title", async ({ page }) => {
    await open(page);
    await expect(handle(page)).toBeVisible();
    const box = await handle(page).boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);
    const title = await card(page).locator("[data-board-card-open]").boundingBox();
    expect(title!.x + title!.width).toBeLessThanOrEqual(box!.x);
    await expect(card(page).getByText("Visible label")).toBeVisible();
    await page.screenshot({ path: ".verification/mobile-drag-native.png" });
});
test("native touch reorders, then cancels without another mutation", async ({ page }) => {
    await open(page);
    const source = await point(handle(page));
    const destination = await card(page, 2).boundingBox();
    await touch(page, "touchStart", source.x, source.y);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "dragging");
    await touch(page, "touchMove", destination!.x + 80, destination!.y + destination!.height - 10);
    await touch(page, "touchEnd", 0, 0);
    expect(await receipts(page)).toContainEqual(expect.objectContaining({ order: 2 }));
    const again = await point(handle(page));
    await touch(page, "touchStart", again.x, again.y);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "dragging");
    await touch(page, "touchCancel", 0, 0);
    await expect(card(page)).not.toHaveAttribute("data-touch-drag-state");
    expect(JSON.parse(await page.locator("#receipts").innerText())).toHaveLength(1);
});
test("body hold and vertical swipe stay outside drag, card title still opens", async ({ page }) => {
    await open(page);
    const body = await point(card(page));
    await touch(page, "touchStart", body.x - 50, body.y);
    await page.waitForTimeout(400);
    await expect(card(page)).not.toHaveAttribute("data-touch-drag-state");
    await touch(page, "touchMove", body.x - 50, body.y - 80);
    await touch(page, "touchEnd", 0, 0);
    await expect(page.locator("#receipts")).toHaveText("[]");
    await card(page).locator("[data-board-card-open]").tap();
    await expect(page).toHaveURL(/mobile-project\/mobile-card-0/);
});
test("native handle reaches another column and empty column at zero order", async ({ page }) => {
    await open(page);
    const start = await point(handle(page));
    await touch(page, "touchStart", start.x, start.y);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "dragging");
    await touch(page, "touchMove", 384, 100);
    await expect.poll(() => page.locator("#mobile-board").evaluate((el) => el.scrollLeft)).toBeGreaterThan(470);
    await touch(page, "touchMove", 180, 450);
    await page.locator("#mobile-board").evaluate((element) => {
        element.scrollLeft = 604;
    });
    await touch(page, "touchMove", 180, 450);
    await touch(page, "touchEnd", 0, 0);
    expect(await receipts(page)).toContainEqual(expect.objectContaining({ parentUID: "empty" }));
    expect(await receipts(page)).toContainEqual(expect.objectContaining({ order: 0 }));
});
for (const query of ["?permission=denied", "?sort=created_newest"])
    test(`native restrictions preserve ${query}`, async ({ page }) => {
        await open(page, query);
        await expect(handle(page)).toHaveCount(0);
        await expect(card(page).locator("[draggable=true]")).toHaveCount(0);
    });

test("native populated-column movement and nonmanual target rejection", async ({ page }) => {
    await open(page);
    let start = await point(handle(page));
    await touch(page, "touchStart", start.x, start.y);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "dragging");
    await page.locator("#mobile-board").evaluate((element) => {
        element.scrollLeft = 302;
    });
    const target = await card(page, 4).boundingBox();
    await touch(page, "touchMove", target!.x + 90, target!.y + 10);
    await touch(page, "touchEnd", 0, 0);
    expect(await receipts(page)).toContainEqual(expect.objectContaining({ parentUID: "destination" }));
    expect(await receipts(page)).toContainEqual(expect.objectContaining({ order: 0 }));
    start = await point(handle(page));
    await touch(page, "touchStart", start.x, start.y);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "dragging");
    await page.locator("#mobile-board").evaluate((element) => {
        element.scrollLeft = 900;
    });
    await touch(page, "touchMove", 250, 450);
    await touch(page, "touchEnd", 0, 0);
    expect(JSON.parse(await page.locator("#receipts").innerText())).toHaveLength(1);
});
test("desktop retains native mouse dragging and hides the touch handle", async ({ browser }) => {
    const context = await browser.newContext({ hasTouch: false, isMobile: false, viewport: { width: 1200, height: 900 } });
    const page = await context.newPage();
    await open(page);
    await expect(handle(page)).toBeHidden();
    const source = card(page).locator("[draggable=true]");
    await expect(source).toHaveCount(1);
    await source.dragTo(card(page, 2), { targetPosition: { x: 90, y: 90 } });
    expect(await receipts(page)).toContainEqual(expect.objectContaining({ rowUID: "mobile-card-0" }));
    await context.close();
});

test("ordinary swipes scroll vertically and horizontally without a drag", async ({ page }) => {
    await open(page);
    const column = page.locator("[data-board-column-touch-dnd-uid=source]");
    await column.evaluate((element) => {
        element.style.height = "250px";
    });
    await touch(page, "touchStart", 100, 230);
    for (let y = 210; y >= 80; y -= 20) await touch(page, "touchMove", 100, y);
    await touch(page, "touchEnd", 0, 0);
    await expect.poll(() => column.evaluate((element) => element.scrollTop)).toBeGreaterThan(20);
    await expect(page.locator("#receipts")).toHaveText("[]");
    await touch(page, "touchStart", 300, 540);
    for (let x = 280; x >= 80; x -= 20) await touch(page, "touchMove", x, 540);
    await touch(page, "touchEnd", 0, 0);
    await expect.poll(() => page.locator("#mobile-board").evaluate((element) => element.scrollLeft)).toBeGreaterThan(20);
    await expect(page.locator("#receipts")).toHaveText("[]");
});
test("early handle movement cancels the pending drag", async ({ page }) => {
    await open(page);
    const start = await point(handle(page));
    await touch(page, "touchStart", start.x, start.y);
    await expect(card(page)).toHaveAttribute("data-touch-drag-state", "pending");
    await touch(page, "touchMove", start.x, start.y + 20);
    await page.waitForTimeout(350);
    await expect(card(page)).not.toHaveAttribute("data-touch-drag-state");
    await touch(page, "touchEnd", 0, 0);
    await expect(page.locator("#receipts")).toHaveText("[]");
});
