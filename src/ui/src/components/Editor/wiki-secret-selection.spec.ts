import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
const source = "same 같은😀 same 같은😀 tail";
const fixture = "/src/components/Editor/wiki-secret-selection.fixture.html";
async function select(page: import("@playwright/test").Page, start = 5, end = 9) {
    await page.locator('[contenteditable="true"]').evaluate(
        (element, { start, end }) => {
            const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
            const node = walker.nextNode()!;
            const range = document.createRange();
            range.setStart(node, start);
            range.setEnd(node, end);
            const selection = window.getSelection()!;
            selection.removeAllRanges();
            selection.addRange(range);
            element.dispatchEvent(new Event("mouseup", { bubbles: true }));
            document.dispatchEvent(new Event("selectionchange"));
        },
        { start, end }
    );
    await expect(page.getByTestId("selection")).toHaveText(
        JSON.stringify({ anchor: { path: [0, 0], offset: start }, focus: { path: [0, 0], offset: end } })
    );
}
test("mounted native editor sends only revision/name/UTF16 metadata then re-reads masked reference", async ({ page }) => {
    const requests: unknown[] = [];
    const result = source.slice(0, 5) + "[••••](secret://ref/fixtureRef)" + source.slice(9);
    await page.route("**/board/fixture-project/wiki/fixture-wiki/secret-selection", async (route) => {
        expect(new URL(route.request().url()).pathname).toBe("/board/fixture-project/wiki/fixture-wiki/secret-selection");
        requests.push(route.request().postDataJSON());
        await route.fulfill({
            json: {
                wiki_uid: "fixture-wiki",
                revision: createHash("sha256").update(result).digest("hex"),
                editor_generation: 1,
                reference: {
                    uri: "secret://ref/fixtureRef",
                    name: "api-key",
                    scope: "project",
                    scope_uid: "fixture-project",
                    state: "active",
                    revision: 0,
                },
            },
        });
    });
    await page.route("**/board/fixture-project/wiki/fixture-wiki", (route) =>
        route.fulfill({ json: { wiki: { uid: "fixture-wiki", editor_generation: 1, content: { content: result } } } })
    );
    await page.goto(fixture);
    await select(page);
    page.once("dialog", (dialog) => dialog.accept("api-key"));
    await page.getByRole("button", { name: "Convert to secret", exact: true }).click();
    await expect(page.getByTestId("status")).toHaveText("Masked reference loaded");
    expect(requests).toEqual([
        { expected_revision: createHash("sha256").update(source).digest("hex"), name: "api-key", selection: { start_utf16: 5, end_utf16: 9 } },
    ]);
    await expect(page.locator('[contenteditable="true"]')).toContainText("••••");
});
test("unsupported backend response preserves the mounted editor draft with fixed safe error", async ({ page }) => {
    await page.route("**/secret-selection", (route) =>
        route.fulfill({ status: 400, json: { error: "unsupported_selection", detail: "must never surface" } })
    );
    await page.goto(fixture);
    await select(page);
    page.once("dialog", (dialog) => dialog.accept("api-key"));
    await page.getByRole("button", { name: "Convert to secret", exact: true }).click();
    await expect(page.getByTestId("status")).toHaveText("Conversion unavailable; draft preserved");
    await expect(page.locator('[contenteditable="true"]')).toContainText(source);
    await expect(page.locator("body")).not.toContainText("must never surface");
});
test("dirty editor disables conversion", async ({ page }) => {
    await page.goto(fixture);
    await page.locator('[contenteditable="true"]').fill("new draft");
    await select(page, 0, 3);
    await expect(page.getByRole("button", { name: "Convert to secret", exact: true })).toBeDisabled();
});
for (const change of ["draft", "session", "selection", "selection-back"] as const) {
    test(`pending result cannot overwrite changed ${change}`, async ({ page }) => {
        let release: () => void = () => {};
        let sent: () => void = () => {};
        const received = new Promise<void>((resolve) => {
            sent = resolve;
        });
        const wait = new Promise<void>((resolve) => {
            release = resolve;
        });
        let reads = 0;
        await page.route("**/secret-selection", async (route) => {
            sent();
            await wait;
            await route.fulfill({ json: { wiki_uid: "fixture-wiki", revision: "ignored", reference: { uri: "secret://ref/fixtureRef" } } });
        });
        await page.route("**/board/fixture-project/wiki/fixture-wiki", async (route) => {
            reads++;
            await route.abort();
        });
        await page.goto(fixture);
        await select(page);
        page.once("dialog", (dialog) => dialog.accept("api-key"));
        await page.getByRole("button", { name: "Convert to secret", exact: true }).click();
        await received;
        if (change === "draft") await page.locator('[contenteditable="true"]').fill("replacement draft");
        if (change === "session") await page.getByRole("button", { name: "End fixture session" }).click();
        if (change === "selection" || change === "selection-back") await select(page, 0, 4);
        if (change === "selection-back") await select(page, 5, 9);
        release();
        await expect(page.getByTestId("status")).toHaveText("Conversion unavailable; draft preserved");
        await expect(page.locator('[contenteditable="true"]')).toContainText(change === "draft" ? "replacement draft" : source);
        expect(reads).toBe(0);
    });
}
test("actual WikiContent owner mounts stored Unicode through native Plate viewer", async ({ page }) => {
    await page.goto(`${fixture}?native-wiki`);
    await expect(page.locator("[data-wiki-content]")).toContainText(source);
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await expect(page.locator('[data-wiki-content] [contenteditable="false"]')).toBeVisible();
});
test("authoritative reread with a different revision cannot replace the draft", async ({ page }) => {
    await page.route("**/secret-selection", (route) =>
        route.fulfill({
            json: {
                wiki_uid: "fixture-wiki",
                revision: "0".repeat(64),
                reference: {
                    uri: "secret://ref/fixtureRef",
                    name: "api-key",
                    scope: "project",
                    scope_uid: "fixture-project",
                    state: "active",
                    revision: 0,
                },
            },
        })
    );
    await page.route("**/board/fixture-project/wiki/fixture-wiki", (route) =>
        route.fulfill({ json: { wiki: { uid: "fixture-wiki", editor_generation: 1, content: { content: "[••••](secret://ref/fixtureRef)" } } } })
    );
    await page.goto(fixture);
    await select(page);
    page.once("dialog", (dialog) => dialog.accept("api-key"));
    await page.getByRole("button", { name: "Convert to secret", exact: true }).click();
    await expect(page.getByTestId("status")).toHaveText("Conversion unavailable; draft preserved");
    await expect(page.locator('[contenteditable="true"]')).toContainText(source);
});
