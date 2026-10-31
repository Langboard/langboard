import { test, expect, type Page } from "@playwright/test";

const fixture = "/src/components/ImagePreviewDialog/imageAnchors.fixture.html";
const revision = "a".repeat(64);

async function placePoint(page: Page, position = { x: 120, y: 80 }) {
    await page.getByRole("button", { name: "Image point", exact: true }).click();
    await page.getByRole("button", { name: "Place image annotation", exact: true }).click({ position });
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(1);
}

async function inspectDraft(page: Page) {
    await page.getByRole("button", { name: "Inspect draft", exact: true }).click();
    return JSON.parse(await page.locator("#draft").innerText());
}

test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => console.error(error.stack));
    await page.route("**/image-anchor.svg*", (route) =>
        route.fulfill({
            contentType: "image/svg+xml",
            body: "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='800'><rect width='1200' height='800' fill='lightblue'/></svg>",
        })
    );
    await page.goto(fixture);
});

test("viewer keyboard focus stays inside and returns to its thumbnail after Escape", async ({ page }) => {
    const thumbnail = page.getByRole("button", { name: "Generic evidence", exact: true });
    await thumbnail.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(":focus")).toHaveCount(1);
    for (let index = 0; index < 12; index++) {
        await page.keyboard.press("Tab");
        await expect(dialog.locator(":focus")).toHaveCount(1);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(thumbnail).toBeFocused();
});

test("attachment identity change with identical URL and revision clears the old pending selection", async ({ page }) => {
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await placePoint(page);
    await page.evaluate(async () => {
        const path = "/src/core/models/ProjectCardAttachment.ts";
        const { Model } = await import(path);
        Model.deleteModel("image-file");
        Model.fromOne(
            {
                created_at: new Date(),
                updated_at: new Date(),
                uid: "replacement-file",
                card_uid: "image-card",
                name: "Evidence.svg",
                url: `${location.origin}/image-anchor.svg`,
                image_revision: "a".repeat(64),
                order: 0,
            },
            true
        );
    });
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Image point", exact: true })).toHaveAttribute("aria-pressed", "false");
    await placePoint(page);
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Replacement attachment comment");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("#request")).toContainText("replacement-file");
    expect(JSON.parse(await page.locator("#request").innerText()).anchor.source).toEqual({
        type: "card_attachment",
        attachment_uid: "replacement-file",
        revision,
    });
});

test("revision change clears placement and closes the old version shortcut with explicit unavailable state", async ({ page }) => {
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await placePoint(page);
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Version A pin");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByRole("button", { name: "Jump saved saved-1", exact: true }).click();
    await placePoint(page, { x: 240, y: 180 });
    await page.evaluate(async () => {
        const path = "/src/core/models/ProjectCardAttachment.ts";
        const { Model } = await import(path);
        Model.fromOne({ uid: "image-file", image_revision: "b".repeat(64) });
    });
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await expect(page.getByRole("alertdialog")).toContainText("Image changed or missing");
    expect(JSON.parse(await page.locator("#request").innerText()).anchor.source.revision).toBe(revision);
});

test("gallery navigation and viewer close keep the existing comment draft and request identity", async ({ page }) => {
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await placePoint(page);
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Original draft remains owned");
    const original = await inspectDraft(page);
    await page.getByRole("button", { name: "Image gallery", exact: true }).click();
    await placePoint(page);
    await page.getByRole("spinbutton").first().fill("2");
    await expect(page.getByRole("dialog").locator("img")).toHaveAttribute("alt", "Second evidence");
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Image point", exact: true })).toHaveCount(0);
    await page.getByRole("spinbutton").first().fill("1");
    await expect(page.getByRole("button", { name: "Image point", exact: true })).toHaveAttribute("aria-pressed", "false");
    await page.keyboard.press("Escape");
    expect(await inspectDraft(page)).toEqual(original);
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await page.keyboard.press("Escape");
    expect(await inspectDraft(page)).toEqual(original);
    await expect(page.locator("#request")).toBeEmpty();
});

test("pending native save retains its submitted image through viewer navigation and restores one pin", async ({ page }) => {
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await placePoint(page);
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Delayed original image save");
    const draft = await inspectDraft(page);
    await page.evaluate(async () => {
        const path = "/src/core/helpers/Api.ts";
        const { api } = await import(path);
        const original = api.defaults.adapter;
        api.defaults.adapter = async (config: { method: string; url?: string }) => {
            if (config.method === "post" && config.url?.includes("/comment")) {
                await new Promise<void>((resolve) => Object.assign(window, { releaseImageSave: resolve }));
            }
            return original(config);
        };
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Image gallery", exact: true }).click();
    await page.getByRole("spinbutton").first().fill("2");
    await expect(page.getByRole("dialog").locator("img")).toHaveAttribute("alt", "Second evidence");
    await page.keyboard.press("Escape");
    expect(await inspectDraft(page)).toEqual(draft);
    await page.evaluate(() => (window as unknown as { releaseImageSave: () => void }).releaseImageSave());
    await expect(page.locator("#request")).toContainText("Delayed original image save");
    const request = JSON.parse(await page.locator("#request").innerText());
    expect(request.request_uid).toBe(draft.request_uid);
    expect(request.anchor).toEqual(draft.anchor);
    await expect(page.getByText("Jump saved", { exact: false })).toHaveCount(1);
    await page.getByRole("button", { name: "Jump saved saved-1", exact: true }).click();
    await expect(page.getByRole("button", { name: "Delayed original image save", exact: true })).toBeVisible();
});

test("comment permission changes while the viewer is open update hover editing without changing the pin", async ({ page }) => {
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await placePoint(page);
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Permission continuity pin");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByRole("button", { name: "Switch actor", exact: true }).click();
    await page.getByRole("button", { name: "Jump saved saved-1", exact: true }).click();
    const pin = page.getByRole("button", { name: "Permission continuity pin", exact: true });
    await pin.hover();
    const hover = page.locator("[data-radix-popper-content-wrapper]:visible");
    await expect(hover.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await expect(hover.getByRole("button", { name: "Open anchored comment", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Toggle admin", exact: true }).evaluate((button: HTMLButtonElement) => button.click());
    await expect(hover.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Toggle admin", exact: true }).evaluate((button: HTMLButtonElement) => button.click());
    await expect(hover.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await pin.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("[data-card-comment-uid=saved-1] [contenteditable=true]")).toHaveCount(0);
    await expect(page.locator("#request")).toBeEmpty();
    await expect(page.getByText("Jump saved", { exact: false })).toHaveCount(1);
});

test("two annotated gallery images use separate attachment identities and discard the previous placement", async ({ page }) => {
    await page.evaluate(async () => {
        const path = "/src/core/models/ProjectCardAttachment.ts";
        const { Model } = await import(path);
        Model.fromOne(
            {
                created_at: new Date(),
                updated_at: new Date(),
                uid: "second-file",
                card_uid: "image-card",
                name: "Second.svg",
                url: `${location.origin}/image-anchor.svg?second=true`,
                image_revision: "b".repeat(64),
                order: 1,
            },
            true
        );
    });
    await page.getByRole("button", { name: "Image gallery", exact: true }).click();
    await placePoint(page);
    await page.getByRole("spinbutton").first().fill("2");
    await expect(page.getByRole("button", { name: "Image point", exact: true })).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await placePoint(page);
    await page.getByRole("button", { name: "Comment", exact: true }).click();
    await page.locator("[contenteditable=true]:visible").fill("Second image pin");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("#request")).toContainText("second-file");
    expect(JSON.parse(await page.locator("#request").innerText()).anchor.source).toEqual({
        type: "card_attachment",
        attachment_uid: "second-file",
        revision: "b".repeat(64),
    });
    await page.getByRole("button", { name: "Image gallery", exact: true }).click();
    await expect(page.getByRole("button", { name: "Second image pin", exact: true })).toHaveCount(0);
    await page.getByRole("spinbutton").first().fill("2");
    await expect(page.getByRole("button", { name: "Second image pin", exact: true })).toBeVisible();
});

test("attachment identity replacement during native capture cancels the old gesture and unlocks zoom", async ({ page }) => {
    await page.getByRole("button", { name: "Native evidence", exact: true }).click();
    await page.getByRole("button", { name: "Image region", exact: true }).click();
    const surface = page.getByRole("button", { name: "Place image annotation", exact: true });
    const box = (await surface.boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    expect(await surface.evaluate((element) => element.hasPointerCapture(1))).toBe(true);
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(1);
    await page.evaluate(async () => {
        const path = "/src/core/models/ProjectCardAttachment.ts";
        const { Model } = await import(path);
        Model.deleteModel("image-file");
        Model.fromOne(
            {
                created_at: new Date(),
                updated_at: new Date(),
                uid: "replacement-file",
                card_uid: "image-card",
                name: "Evidence.svg",
                url: `${location.origin}/image-anchor.svg`,
                image_revision: "a".repeat(64),
                order: 0,
            },
            true
        );
    });
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Image region", exact: true })).toHaveAttribute("aria-pressed", "false");
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6);
    await page.mouse.up();
    await expect(page.locator("[data-pending-image-anchor]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Comment", exact: true })).toHaveCount(0);
    await page.mouse.wheel(0, -100);
    await expect(page.getByRole("spinbutton").last()).toHaveValue("110");
    await expect(page.locator("#request")).toBeEmpty();
});
