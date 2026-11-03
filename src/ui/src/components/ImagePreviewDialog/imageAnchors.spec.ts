import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => console.error(error.stack));
    await page.route("**/image-anchor.svg*", (route) =>
        route.fulfill({
            contentType: "image/svg+xml",
            body: "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='800'><rect width='1200' height='800' fill='lightblue'/></svg>",
        })
    );
});

test("native point composer preserves an ambiguous retry and restores persisted pin", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => (errors.push(error.message), console.error(error.message)));
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).click();
    await page.getByRole("button", { name: "Image point", exact: true }).click();
    const surface = page.getByRole("button", { name: "Place image annotation" });
    await surface.click({ position: { x: 120, y: 80 } });
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    const editor = page.locator("[contenteditable=true]:visible");
    await expect(editor).toBeVisible();
    await editor.fill("Pinned evidence comment");
    await page.getByRole("button", { name: "Fail next response" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("#request")).toContainText("ImageRegionSelector");
    const first = JSON.parse(await page.locator("#request").innerText());
    expect(first.anchor.shape).toBe("point");
    expect(first.anchor.image_width).toBe(1200);
    expect(first.request_uid).toMatch(/^[a-f0-9-]{36}$/);
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(editor).toBeHidden();
    expect(JSON.parse(await page.locator("#request").innerText())).toEqual(first);
    await expect(page.getByText("Jump saved", { exact: false })).toHaveCount(1);
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    await expect(page.getByRole("button", { name: "Pinned evidence comment", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pinned evidence comment", exact: true })).toHaveClass(/ring-4/);
    await page.keyboard.press("ArrowUp");
    await page.setViewportSize({ width: 1000, height: 800 });
    await expect(page.getByRole("button", { name: "Pinned evidence comment", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Replace image" }).click();
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    await expect(page.getByRole("alertdialog")).toContainText("Image changed or missing");
    expect(errors).toEqual([]);
});

test("rectangle keyboard placement and cancel retain no comment", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).click();
    await page.getByRole("button", { name: "Image region", exact: true }).click();
    const surface = page.getByRole("button", { name: "Place image annotation" });
    await surface.focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("ArrowLeft");
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Inspect draft" }).click();
    const draft = JSON.parse(await page.locator("#draft").innerText());
    expect(draft.content).toBe("");
    expect(draft.anchor ?? null).toBeNull();
    await expect(page.getByText("Jump saved", { exact: false })).toHaveCount(0);
});

test("rectangle drag saves original pixels, preserves its placement after zoom and opens native edit", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).click();
    await page.getByRole("button", { name: "Image region", exact: true }).click();
    const surface = page.getByRole("button", { name: "Place image annotation" });
    const box = (await surface.boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.7);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.1);
    await page.mouse.up();
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Rectangle evidence");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("#request")).toContainText("rectangle");
    const { anchor } = JSON.parse(await page.locator("#request").innerText());
    expect(anchor.x).toBeCloseTo(240, 0);
    expect(anchor.y).toBeCloseTo(80, 0);
    expect(anchor.width).toBeCloseTo(720, 0);
    expect(anchor.height).toBeCloseTo(480, 0);
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    const marker = page.getByRole("button", { name: "Rectangle evidence", exact: true });
    const pin = (await marker.boundingBox())!;
    const imageBox = (await page.getByRole("dialog").locator("img").boundingBox())!;
    expect((pin.x - imageBox.x) / imageBox.width).toBeCloseTo(0.2, 2);
    const zoom = page.getByRole("spinbutton").last();
    await zoom.fill("125");
    await zoom.press("ArrowDown");
    await expect(zoom).toHaveValue("124");
    await zoom.blur();
    await page.setViewportSize({ width: 1100, height: 900 });
    const transformed = (await marker.boundingBox())!;
    const resizedImage = (await page.getByRole("dialog").locator("img").boundingBox())!;
    expect((transformed.x - resizedImage.x) / resizedImage.width).toBeCloseTo(0.2, 2);
    await page.mouse.move(resizedImage.x + resizedImage.width * 0.9, resizedImage.y + resizedImage.height * 0.9);
    await page.mouse.down();
    await page.mouse.move(resizedImage.x + resizedImage.width * 0.9 + 20, resizedImage.y + resizedImage.height * 0.9 + 15);
    await page.mouse.up();
    const movedPin = (await marker.boundingBox())!;
    const movedImage = (await page.getByRole("dialog").locator("img").boundingBox())!;
    expect(movedImage.x - resizedImage.x).toBeCloseTo(20, 0);
    expect((movedPin.x - movedImage.x) / movedImage.width).toBeCloseTo(0.2, 2);
    await marker.hover();
    await expect(page.getByRole("button", { name: "Open anchored comment", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Image point", exact: true }).click();
    const currentPin = (await marker.boundingBox())!;
    await page.mouse.move(currentPin.x + currentPin.width / 2, currentPin.y + currentPin.height / 2);
    await page.mouse.down();
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await page.mouse.up();
    await expect(page.locator("[data-comment-edit]")).toBeFocused();
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    await marker.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("[data-comment-edit]")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
    await expect(page.getByText("Syncing draft...", { exact: true })).toBeVisible();
});

test("draft image identity and retry UUID survive reload and refuse replacement", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).click();
    await page.getByRole("button", { name: "Image point", exact: true }).click();
    await page.getByRole("button", { name: "Place image annotation" }).click({ position: { x: 40, y: 60 } });
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Retained draft");
    await page.getByRole("button", { name: "Inspect draft" }).click();
    const before = JSON.parse(await page.locator("#draft").innerText());
    expect(before.anchor.type).toBe("ImageRegionSelector");
    await page.reload();
    await page
        .getByRole("button", { name: /Add a comment as Image Author/ })
        .first()
        .click();
    await expect(page.locator("[contenteditable=true]:visible")).toHaveText("Retained draft");
    await page.getByRole("button", { name: "Inspect draft" }).click();
    expect(JSON.parse(await page.locator("#draft").innerText()).request_uid).toBe(before.request_uid);
    await page.getByRole("button", { name: "Replace image" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("#request")).toBeEmpty();
    await expect(page.locator("[contenteditable=true]:visible")).toHaveText("Retained draft");
});

test("touch pointer capture clamps rectangle to image bounds", async ({ browser }) => {
    const context = await browser.newContext({ hasTouch: true, viewport: { width: 1000, height: 800 } });
    const page = await context.newPage();
    await page.route("**/image-anchor.svg*", (route) =>
        route.fulfill({
            contentType: "image/svg+xml",
            body: "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='800'><rect width='1200' height='800' fill='lightblue'/></svg>",
        })
    );
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).tap();
    await page.getByRole("button", { name: "Image region", exact: true }).tap();
    const box = (await page.getByRole("button", { name: "Place image annotation" }).boundingBox())!;
    const cdp = await context.newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: box.x + box.width + 10, y: box.y + box.height + 10 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.getByRole("button", { name: "Comment", exact: true }).tap();
    await page.locator("[contenteditable=true]:visible").fill("Touch rectangle");
    await page.getByRole("button", { name: "Save", exact: true }).tap();
    await expect(page.locator("#request")).toContainText("rectangle");
    const { anchor } = JSON.parse(await page.locator("#request").innerText());
    expect(anchor.x).toBeCloseTo(600, 0);
    expect(anchor.y).toBeCloseTo(400, 0);
    expect(anchor.x + anchor.width).toBeCloseTo(1200, 0);
    expect(anchor.y + anchor.height).toBeCloseTo(800, 0);
    await context.close();
});

test("generic URL compatibility and text anchors preserve native composer behavior", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Generic evidence" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("button", { name: "Image point", exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Text anchor", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Text comment retained");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("#request")).toContainText("TextQuoteSelector");
    const request = JSON.parse(await page.locator("#request").innerText());
    expect(request.request_uid).toBeUndefined();
    expect(request.anchor.exact).toBe("Text evidence");
    await expect(page.getByRole("button", { name: "“Text evidence”" })).toBeVisible();
});

test("draft metadata remains owned by actor and card across composer placements", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).click();
    await page.getByRole("button", { name: "Image point", exact: true }).click();
    await page.getByRole("button", { name: "Place image annotation" }).click({ position: { x: 40, y: 60 } });
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Owned draft");
    await page.getByRole("button", { name: "Inspect draft" }).click();
    const owned = JSON.parse(await page.locator("#draft").innerText());
    await page.setViewportSize({ width: 700, height: 900 });
    await expect(page.locator("[contenteditable=true]:visible")).toHaveText("Owned draft");
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(page.locator("[contenteditable=true]:visible")).toHaveText("Owned draft");
    await page.getByRole("button", { name: "Switch actor" }).click();
    await page.getByRole("button", { name: "Inspect draft" }).click();
    expect(JSON.parse(await page.locator("#draft").innerText()).anchor).toBeFalsy();
    await page.getByRole("button", { name: "Switch actor" }).click();
    await page.getByRole("button", { name: "Inspect draft" }).click();
    expect(JSON.parse(await page.locator("#draft").innerText()).request_uid).toBe(owned.request_uid);
    await page.getByRole("button", { name: "Switch card" }).click();
    await page.getByRole("button", { name: "Inspect draft" }).click();
    expect(JSON.parse(await page.locator("#draft").innerText()).anchor).toBeFalsy();
    await page.getByRole("button", { name: "Native evidence" }).click();
    await expect(page.getByRole("button", { name: "Image point", exact: true })).toHaveCount(0);
});

test("legacy string drafts retain JSON text and isolated actor/project/card ownership", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    const snapshots = await page.evaluate(async () => {
        const path = "/src/core/stores/CardCommentDraftStore.ts";
        const { getCardCommentDraftStore } = await import(path);
        const legacy = JSON.stringify({ content: "literal legacy editor text" });
        sessionStorage.setItem("comment-legacy-project-card", legacy);
        const store = getCardCommentDraftStore();
        const restored = store.getSnapshot("legacy", "project", "card");
        store.saveSnapshot("a", "p", "c", { content: "one", request_uid: "id" });
        return {
            restored,
            otherActor: store.getSnapshot("b", "p", "c"),
            otherProject: store.getSnapshot("a", "q", "c"),
            otherCard: store.getSnapshot("a", "p", "d"),
        };
    });
    expect(snapshots.restored.content).toBe(JSON.stringify({ content: "literal legacy editor text" }));
    expect(snapshots.otherActor.content).toBe("");
    expect(snapshots.otherProject.content).toBe("");
    expect(snapshots.otherCard.content).toBe("");
});

test("hover Edit activates only the authorized native editor while Open stays distinct", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await page.getByRole("button", { name: "Image point", exact: true }).click();
    await page.getByRole("button", { name: "Place image annotation" }).click({ position: { x: 120, y: 80 } });
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Hover edit evidence");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const comment = page.locator("[data-card-comment-uid=saved-1]");
    const marker = page.getByRole("button", { name: "Hover edit evidence", exact: true });
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    await marker.hover();
    const hover = page.locator("[data-radix-popper-content-wrapper]:visible");
    await expect(hover.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await hover.getByRole("button", { name: "Open anchored comment", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(comment.locator("[contenteditable=true]")).toHaveCount(0);
    await expect(comment.locator("[data-comment-edit]")).toBeFocused();
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    const zoom = page.getByRole("spinbutton").last();
    await zoom.fill("125");
    await zoom.blur();
    await marker.hover();
    await hover.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(comment).toContainText("Hover edit evidence");
    await expect(comment.getByText("Syncing draft...", { exact: true })).toBeVisible();
    await expect(comment.getByRole("button", { name: "Save", exact: true })).toBeVisible();
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await comment.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Switch actor", exact: true }).click();
    await page.getByRole("button", { name: "Read only", exact: true }).click();
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    await marker.hover();
    await expect(hover.getByRole("button", { name: "Open anchored comment", exact: true })).toBeVisible();
    await expect(hover.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await hover.getByRole("button", { name: "Open anchored comment", exact: true }).click();
    await expect(comment.locator("[contenteditable=true]")).toHaveCount(0);
    await page.getByRole("button", { name: "Toggle admin", exact: true }).click();
    await page.getByRole("button", { name: "Jump saved saved-1" }).click();
    await marker.hover();
    await hover.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(comment.getByRole("button", { name: "Save", exact: true })).toBeVisible();
});

test("secondary touch cannot replace or finish an active rectangle", async ({ browser }) => {
    const context = await browser.newContext({ hasTouch: true, viewport: { width: 1000, height: 800 } });
    const page = await context.newPage();
    await page.route("**/image-anchor.svg*", (route) =>
        route.fulfill({
            contentType: "image/svg+xml",
            body: "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='800'><rect width='1200' height='800' fill='lightblue'/></svg>",
        })
    );
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).tap();
    await page.getByRole("button", { name: "Image region", exact: true }).tap();
    const box = (await page.getByRole("button", { name: "Place image annotation" }).boundingBox())!;
    await page.evaluate(() => {
        const events: { type: string; pointerId: number; primary: boolean }[] = [];
        Object.assign(window, { imageTouchEvents: events });
        for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {
            document.addEventListener(
                type,
                (event) => {
                    const pointer = event as PointerEvent;
                    events.push({ type, pointerId: pointer.pointerId, primary: pointer.isPrimary });
                },
                true
            );
        }
    });
    const first = { id: 1, x: box.x + box.width * 0.2, y: box.y + box.height * 0.2 };
    const second = { id: 2, x: box.x + box.width * 0.8, y: box.y + box.height * 0.8 };
    const cdp = await context.newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [first] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [first, second] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [second] });
    await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ ...first, x: box.x + box.width * 0.5, y: box.y + box.height * 0.5 }],
    });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    const events = await page.evaluate(
        () => (window as unknown as { imageTouchEvents: { type: string; pointerId: number; primary: boolean }[] }).imageTouchEvents
    );
    console.log("Native multi-touch pointer trace:", JSON.stringify(events));
    expect(events.map(({ type, primary }) => ({ type, primary }))).toEqual([
        { type: "pointerdown", primary: true },
        { type: "pointerdown", primary: false },
        { type: "pointerup", primary: false },
        { type: "pointermove", primary: true },
        { type: "pointerup", primary: true },
    ]);
    expect(events[3].pointerId).toBe(events[0].pointerId);
    expect(events[4].pointerId).toBe(events[0].pointerId);
    await expect(page.getByRole("button", { name: "Comment", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Comment", exact: true }).tap();
    await page.locator("[contenteditable=true]:visible").fill("Primary touch rectangle");
    await page.getByRole("button", { name: "Save", exact: true }).tap();
    const { anchor } = JSON.parse(await page.locator("#request").innerText());
    expect(anchor.x).toBeCloseTo(240, 0);
    expect(anchor.y).toBeCloseTo(160, 0);
    expect(anchor.width).toBeCloseTo(360, 0);
    expect(anchor.height).toBeCloseTo(240, 0);
    await context.close();
});

test("wheel during rectangle drag preserves its coordinate frame and resumes zoom after release", async ({ page }) => {
    await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
    await page.getByRole("button", { name: "Native evidence" }).click();
    await page.getByRole("button", { name: "Image region", exact: true }).click();
    const surface = page.getByRole("button", { name: "Place image annotation" });
    const box = (await surface.boundingBox())!;
    const zoom = page.getByRole("spinbutton").last();
    await expect(zoom).toHaveValue("100");
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.7);
    await page.mouse.wheel(0, -100);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect.soft(await zoom.inputValue()).toBe("100");
    await page.keyboard.press("ArrowUp");
    await expect(zoom).toHaveValue("100");
    await page.mouse.up();
    await page.mouse.wheel(0, -100);
    await expect(zoom).toHaveValue("110");
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Wheel-stable rectangle");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const { anchor } = JSON.parse(await page.locator("#request").innerText());
    console.log("Rectangle wheel coordinates:", JSON.stringify(anchor));
    expect.soft(anchor.x).toBeCloseTo(240, 0);
    expect.soft(anchor.y).toBeCloseTo(160, 0);
    expect.soft(anchor.width).toBeCloseTo(600, 0);
    expect.soft(anchor.height).toBeCloseTo(400, 0);
});

for (const cancellation of ["Escape", "Cancel", "mode switch", "lost capture"] as const) {
    test(`${cancellation} during pointer capture cancels selection and immediately unlocks zoom`, async ({ page }) => {
        await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
        await page.getByRole("button", { name: "Native evidence" }).click();
        await page.getByRole("button", { name: "Image region", exact: true }).click();
        const surface = page.getByRole("button", { name: "Place image annotation" });
        const box = (await surface.boundingBox())!;
        await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6);
        await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(1);
        const pointerId = await surface.evaluate((element) => {
            for (let id = 1; id < 10; id++) if (element.hasPointerCapture(id)) return id;
            return null;
        });
        expect(pointerId).not.toBeNull();
        if (cancellation === "Escape") await page.keyboard.press("Escape");
        else if (cancellation === "lost capture") {
            await surface.evaluate((element, id) => element.releasePointerCapture(id!), pointerId);
            await page.mouse.move(box.x + box.width * 0.65, box.y + box.height * 0.65);
        } else {
            await page.getByRole("button", { name: cancellation === "Cancel" ? "Cancel" : "Image point", exact: true }).focus();
            await page.keyboard.press("Enter");
        }
        await expect(page.getByRole("dialog")).toBeVisible();
        await expect.soft(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
        const zoom = page.getByRole("spinbutton").last();
        await page.mouse.wheel(0, -100);
        await expect.soft(zoom).toHaveValue("110");
        await page.mouse.up();
        await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Comment", exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "Image region", exact: true }).click();
        const nextBox = (await surface.boundingBox())!;
        await page.mouse.move(nextBox.x + nextBox.width * 0.2, nextBox.y + nextBox.height * 0.2);
        await page.mouse.down();
        await page.mouse.move(nextBox.x + nextBox.width * 0.5, nextBox.y + nextBox.height * 0.5);
        await page.mouse.up();
        await expect(page.getByRole("button", { name: "Comment", exact: true })).toBeEnabled();
    });
}

for (const control of ["numeric zoom", "Fit image"] as const) {
    test(`${control} during rectangle drag preserves its transform and resumes after release`, async ({ page }) => {
        await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
        await page.getByRole("button", { name: "Native evidence" }).click();
        const zoom = page.getByRole("spinbutton").last();
        await zoom.fill("125");
        await zoom.blur();
        const image = page.getByRole("dialog").locator("img");
        const imageBox = (await image.boundingBox())!;
        await page.mouse.move(imageBox.x + imageBox.width / 2, imageBox.y + imageBox.height / 2);
        await page.mouse.down();
        await page.mouse.move(imageBox.x + imageBox.width / 2 + 35, imageBox.y + imageBox.height / 2 + 25);
        await page.mouse.up();
        await page.getByRole("button", { name: "Image region", exact: true }).click();
        const surface = page.getByRole("button", { name: "Place image annotation" });
        const box = (await surface.boundingBox())!;
        await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
        expect(await surface.evaluate((element) => element.hasPointerCapture(1))).toBe(true);
        if (control === "numeric zoom") await zoom.fill("150");
        else {
            await page.getByRole("button", { name: "Fit image", exact: true }).focus();
            await page.keyboard.press("Enter");
        }
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        expect.soft(await zoom.inputValue()).toBe("125");
        expect.soft(await image.boundingBox()).toEqual(box);
        await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6);
        await page.mouse.up();
        await page.getByRole("button", { name: "Comment", exact: true }).focus();
        await page.keyboard.press("Enter");
        await page.locator("[contenteditable=true]:visible").fill(`${control} stable rectangle`);
        await page.getByRole("button", { name: "Save", exact: true }).click();
        const { anchor } = JSON.parse(await page.locator("#request").innerText());
        console.log(`${control} rectangle coordinates:`, JSON.stringify(anchor));
        expect.soft(anchor.x).toBeCloseTo(240, 0);
        expect.soft(anchor.y).toBeCloseTo(160, 0);
        expect.soft(anchor.width).toBeCloseTo(480, 0);
        expect.soft(anchor.height).toBeCloseTo(320, 0);
        await page.getByRole("button", { name: "Jump saved saved-1" }).click();
        await zoom.fill("150");
        await expect(zoom).toHaveValue("150");
        await page.getByRole("button", { name: "Fit image", exact: true }).click();
        await expect(zoom).toHaveValue("100");
        const fitted = (await image.boundingBox())!;
        const viewport = page.viewportSize()!;
        expect(fitted.x + fitted.width / 2).toBeCloseTo(viewport.width / 2, 0);
        expect(fitted.y + fitted.height / 2).toBeCloseTo(viewport.height / 2, 0);
    });
}

for (const control of ["keyboard", "image number", "next button"] as const) {
    test(`${control} cannot change the image during an annotation gesture`, async ({ page }) => {
        await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
        await page.getByRole("button", { name: "Image gallery", exact: true }).click();
        await page.getByRole("button", { name: "Image region", exact: true }).click();
        const surface = page.getByRole("button", { name: "Place image annotation" });
        const box = (await surface.boundingBox())!;
        await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6);
        const imageNumber = page.getByRole("spinbutton").first();
        if (control === "keyboard") {
            await page.getByRole("button", { name: "Image region", exact: true }).focus();
            await page.keyboard.press("ArrowRight");
        } else if (control === "image number") await imageNumber.fill("2");
        else
            await page
                .getByRole("dialog")
                .locator("button:has(svg.lucide-chevron-right)")
                .evaluate((button: HTMLButtonElement) => button.click());
        await expect(imageNumber).toHaveValue("1");
        await expect(page.getByRole("dialog").locator("img")).toHaveAttribute("alt", "First evidence");
        await page.mouse.up();
        await expect(page.getByRole("button", { name: "Comment", exact: true })).toBeEnabled();
        await imageNumber.fill("2");
        await expect(imageNumber).toHaveValue("2");
        await expect(page.getByRole("dialog").locator("img")).toHaveAttribute("alt", "Second evidence");
    });
}

for (const key of ["Enter", "Space"] as const) {
    test(`${key} on a saved pin during placement opens its comment without creating a selection`, async ({ page }) => {
        await page.goto("/src/components/ImagePreviewDialog/imageAnchors.fixture.html");
        await page.getByRole("button", { name: "Native evidence", exact: true }).click();
        await page.getByRole("button", { name: "Image point", exact: true }).click();
        await page.getByRole("button", { name: "Place image annotation" }).click({ position: { x: 120, y: 80 } });
        await page.getByRole("button", { name: "Comment", exact: true }).click();
        await page.locator("[contenteditable=true]:visible").fill("Keyboard pin evidence");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await page.getByRole("button", { name: "Jump saved saved-1" }).click();
        await page.getByRole("button", { name: "Image point", exact: true }).click();
        const marker = page.getByRole("button", { name: "Keyboard pin evidence", exact: true });
        await marker.focus();
        await page.keyboard.press(key);
        await expect.soft(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(page.locator("[data-comment-edit]")).toBeFocused();
        await expect(page.getByText("Jump saved", { exact: false })).toHaveCount(1);
    });
}
