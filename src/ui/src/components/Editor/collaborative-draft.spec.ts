import { expect, test } from "@playwright/test";

for (const width of [1280, 390]) {
    test(`two native owner peers retain one body through reopen at ${width}px`, async ({ page, context }) => {
        await page.setViewportSize({ width, height: 844 });
        const fixture = `/src/components/Editor/collaborative-draft.fixture.html?card=owner-two-peer-${width}`;
        const other = await context.newPage();
        await other.setViewportSize({ width, height: 844 });
        await Promise.all([page.goto(fixture), other.goto(fixture)]);
        const first = page.locator("[contenteditable=true]");
        const second = other.locator("[contenteditable=true]");
        await expect(page.getByTestId("ready")).toHaveText("ready");
        await expect(other.getByTestId("ready")).toHaveText("ready");
        await expect(first).toHaveText("Initial server content");
        await expect(second).toHaveText("Initial server content");
        await first.fill("Initial server content with peer A unsaved marker");
        await expect(second).toHaveText("Initial server content with peer A unsaved marker");
        await other.getByRole("button", { name: "Delayed server update", exact: true }).click();
        await expect(second).toHaveText("Initial server content with peer A unsaved marker");
        await page.reload();
        await expect(page.getByTestId("ready")).toHaveText("ready");
        await expect(first).toHaveText("Initial server content with peer A unsaved marker");
        await expect(second).toHaveText("Initial server content with peer A unsaved marker");
        await other.close();
    });
}

for (const width of [1280, 390]) {
    test(`delayed server snapshot preserves unsaved Yjs draft at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(`/src/components/Editor/collaborative-draft.fixture.html?card=draft-${width}`);
        await expect(page.getByTestId("ready")).toHaveText("ready");
        const editor = page.locator("[contenteditable=true]");
        await expect(editor).toContainText("Initial server content");
        await editor.press("End");
        await editor.pressSequentially(" local unsaved draft");
        await page.getByRole("button", { name: "Delayed server update" }).click();
        await expect(editor).toContainText("local unsaved draft");
        await expect(editor).not.toContainText("Delayed server content");
        await page.getByRole("button", { name: "Explicit reset", exact: true }).click();
        await expect(editor).toContainText("Explicit restored content");
        expect(errors).toEqual([]);
    });
}

for (const width of [1280, 390]) {
    test(`unsynchronized draft cannot accept keyboard edits at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.routeWebSocket(/127\.0\.0\.1:4243/, () => {});
        await page.goto(`/src/components/Editor/collaborative-draft.fixture.html?card=waiting-${width}`);
        await expect(page.getByTestId("ready")).toHaveText("waiting");
        const editor = page.locator("[data-slate-editor]");
        await expect(editor).toContainText("Initial server content");
        await expect(editor).toHaveAttribute("contenteditable", "false");
        await editor.press("Tab");
        await page.keyboard.type("must not enter the unsynchronized draft");
        await expect(editor).not.toContainText("must not enter");
    });
}

test("another tab's comment clear supersedes a cached draft", async ({ page, context }) => {
    const fixture = "/src/components/Editor/collaborative-draft.fixture.html?card=tab-clear";
    await page.goto(fixture);
    await expect(page.getByTestId("ready")).toHaveText("ready");
    const otherTab = await context.newPage();
    await otherTab.goto(fixture);
    await expect(otherTab.getByTestId("ready")).toHaveText("ready");
    const write = async (content: string) =>
        page.evaluate(async (value) => {
            const modulePath = "/src/core/stores/CardCommentDraftStore.ts";
            const { getCardCommentDraftStore } = await import(modulePath);
            getCardCommentDraftStore().saveDraft("draft-actor", "draft-project", "draft-card", value);
        }, content);
    const read = async () =>
        page.evaluate(async () => {
            const modulePath = "/src/core/stores/CardCommentDraftStore.ts";
            const { getCardCommentDraftStore } = await import(modulePath);
            return getCardCommentDraftStore().getDraft("draft-actor", "draft-project", "draft-card");
        });
    await write("submitted in another tab");
    expect(await read()).toBe("submitted in another tab");
    await otherTab.evaluate(async () => {
        const modulePath = "/src/core/stores/CardCommentDraftStore.ts";
        const { getCardCommentDraftStore } = await import(modulePath);
        getCardCommentDraftStore().clearDraft("draft-actor", "draft-project", "draft-card");
    });
    await expect.poll(read).toBe("");
    await write("new unsent comment");
    expect(await read()).toBe("new unsent comment");
});

test("draft storage events tolerate denied access and ignore unrelated storage", async ({ page }) => {
    await page.goto("/src/components/Editor/collaborative-draft.fixture.html?card=storage-boundaries");
    await expect(page.getByTestId("ready")).toHaveText("ready");
    const result = await page.evaluate(async () => {
        const modulePath = "/src/core/stores/CardDraftStore.ts";
        const { getCardDraftStore, buildDraftScopeKey } = await import(modulePath);
        const scope = { userUID: "event-actor", projectUID: "event-project", kind: "comment-new", scopeUID: "event-card" };
        const key = buildDraftScopeKey(scope);
        getCardDraftStore().saveDraft(scope, "retained");
        const cached = () => getCardDraftStore().cache[key]?.content;
        window.dispatchEvent(new StorageEvent("storage", { key, storageArea: sessionStorage }));
        const afterSession = cached();
        window.dispatchEvent(new StorageEvent("storage", { key: "unrelated", storageArea: localStorage }));
        const afterUnrelated = cached();
        const storageDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage")!;
        const errors: string[] = [];
        const onError = (event: ErrorEvent) => {
            errors.push(event.message);
            event.preventDefault();
        };
        window.addEventListener("error", onError);
        Object.defineProperty(window, "localStorage", {
            configurable: true,
            get() {
                throw new DOMException("Denied", "SecurityError");
            },
        });
        try {
            window.dispatchEvent(new StorageEvent("storage", { key }));
        } finally {
            Object.defineProperty(window, "localStorage", storageDescriptor);
            window.removeEventListener("error", onError);
        }
        const afterDenied = cached();
        window.dispatchEvent(new StorageEvent("storage", { key: null, storageArea: localStorage }));
        return { afterSession, afterUnrelated, afterDenied, errors, afterClear: cached() ?? null };
    });
    expect(result).toEqual({ afterSession: "retained", afterUnrelated: "retained", afterDenied: "retained", errors: [], afterClear: null });
});
