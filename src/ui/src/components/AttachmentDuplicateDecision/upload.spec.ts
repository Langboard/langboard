import { expect, test, type Page, type Route } from "@playwright/test";
const path = "/src/components/AttachmentDuplicateDecision/upload.fixture.html";
type Source = "attachment" | "editor";
interface Upload {
    body: string;
    requestUID: string;
    action?: string;
    token?: string;
    replacement?: string;
}
const field = (body: string, name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]+)`))?.[1];
function parse(route: Route): Upload {
    const body = route.request().postData() ?? "";
    return {
        body,
        requestUID: field(body, "request_uid")!,
        action: field(body, "duplicate_action"),
        token: field(body, "confirmation_token"),
        replacement: field(body, "replacement_uid"),
    };
}
const conflict = (requestUID: string, token = "current-token", empty = false) => ({
    confirmation_required: true,
    request_uid: requestUID,
    confirmation_token: token,
    conflicts: empty
        ? []
        : [
              { uid: "replaceable", name: "existing.txt", same_name: true, same_content: true, can_replace: true },
              { uid: "protected", name: "protected.txt", same_name: false, same_content: true, can_replace: false },
          ],
});
const success = (requestUID: string, uid = "uploaded") => ({
    uid,
    request_uid: requestUID,
    card_uid: "fixture-card",
    name: "sample.txt",
    url: "/file/uploaded.txt",
    order: 0,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
});
async function intercept(page: Page, handler: (upload: Upload, count: number) => { status: number; json: unknown } | "network") {
    const calls: Upload[] = [];
    await page.route("**/board/fixture/card/*/attachment", async (route) => {
        const upload = parse(route);
        calls.push(upload);
        const result = handler(upload, calls.length);
        if (result === "network") return route.abort("failed");
        return route.fulfill(result);
    });
    return calls;
}
async function start(page: Page, source: Source, files = ["sample.txt"]) {
    if (source === "attachment") {
        await page.getByRole("button", { name: "Attach file", exact: true }).click();
        const chooser = page.waitForEvent("filechooser");
        await page.getByRole("button", { name: "Upload a file", exact: true }).click();
        await (await chooser).setFiles(files.map((name) => ({ name, mimeType: "text/plain", buffer: Buffer.from("same bytes") })));
        await page.getByRole("button", { name: "Save", exact: true }).click();
    } else {
        const chooser = page.waitForEvent("filechooser");
        await page.getByText("Add a file", { exact: true }).click();
        await (await chooser).setFiles(files.map((name) => ({ name, mimeType: "text/plain", buffer: Buffer.from("same bytes") })));
    }
}
async function editorValue(page: Page) {
    return page.evaluate(() => (window as unknown as { readUploadEditor: () => unknown }).readUploadEditor());
}
for (const source of ["attachment", "editor"] as const) {
    for (const action of ["add", "replace", "cancel"] as const) {
        test(`${source}: explicit ${action} keeps one UUID and binds exact file`, async ({ page }) => {
            const calls = await intercept(page, (upload, count) => ({
                status: count === 1 ? 409 : 201,
                json: count === 1 ? conflict(upload.requestUID) : success(upload.requestUID),
            }));
            await page.goto(path);
            const before = await editorValue(page);
            await start(page, source);
            const dialog = page.getByRole("dialog", { name: "Duplicate attachment", exact: true });
            await expect(dialog).toContainText("sample.txt");
            await expect(dialog.getByRole("button", { name: "Replace protected.txt", exact: true })).toHaveCount(0);
            expect(calls).toHaveLength(1);
            expect(calls[0].requestUID).toMatch(/^[0-9a-f-]{36}$/);
            expect(calls[0].action).toBeUndefined();
            await expect(page.getByTestId("callbacks")).toHaveText("0");
            await dialog
                .getByRole("button", {
                    name: action === "add" ? "Add separate attachment" : action === "replace" ? "Replace existing.txt" : "Cancel",
                    exact: true,
                })
                .click();
            await expect(dialog).toHaveCount(0);
            if (action === "cancel") {
                expect(calls).toHaveLength(1);
                expect(await editorValue(page)).toEqual(before);
                await expect(page.getByTestId("callbacks")).toHaveText("0");
            } else {
                await expect.poll(() => calls.length).toBe(2);
                expect(calls[1]).toMatchObject({ requestUID: calls[0].requestUID, action, token: "current-token" });
                expect(calls[1].replacement).toBe(action === "replace" ? "replaceable" : undefined);
                expect(calls[1].body).toContain("same bytes");
                await page
                    .getByRole("button", { name: "Read models", exact: true, includeHidden: true })
                    .evaluate((button: HTMLButtonElement) => button.click());
                await expect(page.getByTestId("models")).toHaveText(JSON.stringify(["sample.txt"]));
                if (source === "editor") {
                    await expect(page.getByTestId("callbacks")).toHaveText("1");
                    expect(JSON.stringify(await editorValue(page))).toContain("/file/uploaded.txt");
                }
            }
        });
    }
    test(`${source}: changed token and empty conflict state require fresh Add consent`, async ({ page }) => {
        const calls = await intercept(page, (upload, count) => ({
            status: count < 3 ? 409 : 201,
            json: count < 3 ? conflict(upload.requestUID, count === 1 ? "old" : "fresh", count === 2) : success(upload.requestUID),
        }));
        await page.goto(path);
        await start(page, source);
        await page.getByRole("button", { name: "Replace existing.txt", exact: true }).click();
        await expect(page.getByRole("button", { name: "Replace existing.txt", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Add separate attachment", exact: true })).toBeVisible();
        expect(calls).toHaveLength(2);
        await page.getByRole("button", { name: "Add separate attachment", exact: true }).click();
        await expect.poll(() => calls.length).toBe(3);
        expect(calls[2]).toMatchObject({ requestUID: calls[0].requestUID, action: "add", token: "fresh" });
        expect(calls[2].replacement).toBeUndefined();
    });
    test(`${source}: ambiguous network retry preserves exact request`, async ({ page }) => {
        const calls = await intercept(page, (upload, count) => (count === 1 ? "network" : { status: 201, json: success(upload.requestUID) }));
        await page.goto(path);
        await start(page, source);
        if (source === "attachment") {
            await expect(page.getByText("1 files could not be uploaded.", { exact: true }).first()).toBeVisible();
            await page.getByRole("button", { name: "Save", exact: true }).click();
        } else {
            await page.getByRole("button", { name: "Retry", exact: true }).click();
        }
        await expect.poll(() => calls.length).toBe(2);
        expect(calls[1].requestUID).toBe(calls[0].requestUID);
        expect(calls[1].body).toContain("same bytes");
    });
    for (const status of [403, 404, 409, 201]) {
        test(`${source}: malformed/private ${status} cannot mutate models or editor`, async ({ page }) => {
            const calls = await intercept(page, () => ({
                status,
                json: { private_payload: "private-server-value", confirmation_required: true, confirmation_token: "untrusted" },
            }));
            await page.goto(path);
            const before = await editorValue(page);
            await start(page, source);
            if (source === "attachment") await expect(page.getByText("1 files could not be uploaded.", { exact: true }).first()).toBeVisible();
            else await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
            await expect(page.getByText("private-server-value", { exact: false })).toHaveCount(0);
            await expect(page.getByRole("dialog", { name: "Duplicate attachment", exact: true })).toHaveCount(0);
            await expect(page.getByTestId("callbacks")).toHaveText("0");
            expect(await editorValue(page)).toEqual(before);
            await page
                .getByRole("button", { name: "Read models", exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
            await expect(page.getByTestId("models")).toHaveText("[]");
            expect(calls).toHaveLength(1);
        });
    }
    for (const teardown of ["Change source", "Change account", "Unmount upload"]) {
        test(`${source}: ${teardown} invalidates waiting consent`, async ({ page }) => {
            const calls = await intercept(page, (upload) => ({ status: 409, json: conflict(upload.requestUID) }));
            await page.goto(path);
            await start(page, source);
            await expect(page.getByRole("button", { name: "Add separate attachment", exact: true })).toBeVisible();
            await page
                .getByRole("button", { name: teardown, exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
            await expect(page.getByRole("dialog", { name: "Duplicate attachment", exact: true })).toHaveCount(0);
            expect(calls).toHaveLength(1);
            await expect(page.getByTestId("callbacks")).toHaveText("0");
        });
    }
}

for (const source of ["attachment", "editor"] as const) {
    test(`multiple ${source} decisions resubmit only their selected file`, async ({ page }) => {
        const calls = await intercept(page, (upload) =>
            upload.action ? { status: 201, json: success(upload.requestUID) } : { status: 409, json: conflict(upload.requestUID) }
        );
        await page.goto(path);
        await start(page, source, ["first.txt", "second.txt"]);
        await expect(page.locator("[role=dialog][data-dialog-content]")).toHaveCount(1);
        await expect.poll(() => calls.length).toBe(2);
        const dialog = page.getByRole("dialog", { name: "Duplicate attachment", exact: true });
        const text = (await dialog.textContent()) ?? "";
        const selectedName = text.includes("first.txt") ? "first.txt" : "second.txt";
        expect(calls).toHaveLength(2);
        expect(new Set(calls.map((call) => call.requestUID)).size).toBe(2);
        const selected = calls.find((call) => call.body.includes(`filename="${selectedName}"`))!;
        await dialog.getByRole("button", { name: "Add separate attachment", exact: true }).click();
        await expect.poll(() => calls.length).toBe(3);
        expect(calls[2]).toMatchObject({ requestUID: selected.requestUID, action: "add", token: "current-token" });
        expect(calls[2].body).toContain(`filename="${selectedName}"`);
        const remaining = page.getByRole("dialog", { name: "Duplicate attachment", exact: true });
        await remaining.getByRole("button", { name: "Cancel", exact: true }).click();
        expect(calls).toHaveLength(3);
    });
}

for (const source of ["attachment", "editor"] as const) {
    for (const teardown of ["Change source", "Change account"]) {
        test(`${source}: ${teardown} aborts an in-flight successful response`, async ({ page }) => {
            let release!: () => void;
            const gate = new Promise<void>((resolve) => {
                release = resolve;
            });
            const calls: Upload[] = [];
            await page.route("**/board/fixture/card/*/attachment", async (route) => {
                const upload = parse(route);
                calls.push(upload);
                await gate;
                await route.fulfill({ status: 201, json: success(upload.requestUID, "stale-upload") }).catch(() => {});
            });
            await page.goto(path);
            await start(page, source);
            await expect.poll(() => calls.length).toBe(1);
            await page
                .getByRole("button", { name: teardown, exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
            release();
            await page
                .getByRole("button", { name: "Read models", exact: true, includeHidden: true })
                .evaluate((button: HTMLButtonElement) => button.click());
            await expect(page.getByTestId("models")).toHaveText("[]");
            await expect(page.getByTestId("callbacks")).toHaveText("0");
            expect(JSON.stringify(await editorValue(page))).not.toContain("/file/uploaded.txt");
        });
    }
}

test("mobile duplicate decision exposes exact targets and explicit cancellation", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const calls = await intercept(page, (upload) => ({ status: 409, json: conflict(upload.requestUID) }));
    await page.goto(path);
    await start(page, "editor");
    const dialog = page.getByRole("dialog", { name: "Duplicate attachment", exact: true });
    await expect(dialog.getByRole("button", { name: "Replace existing.txt", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(calls).toHaveLength(1);
});

for (const source of ["attachment", "editor"] as const) {
    test(`${source}: retry after an ambiguous replacement retains chosen action and token`, async ({ page }) => {
        const calls = await intercept(page, (upload, count) =>
            count === 1
                ? { status: 409, json: conflict(upload.requestUID) }
                : count === 2
                  ? "network"
                  : { status: 200, json: success(upload.requestUID) }
        );
        await page.goto(path);
        await start(page, source);
        await page.getByRole("button", { name: "Replace existing.txt", exact: true }).click();
        if (source === "attachment") {
            await expect(page.getByText("1 files could not be uploaded.", { exact: true }).first()).toBeVisible();
            await page.getByRole("button", { name: "Save", exact: true }).click();
        } else await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect.poll(() => calls.length).toBe(3);
        expect(calls[2]).toMatchObject({ requestUID: calls[0].requestUID, action: "replace", token: "current-token", replacement: "replaceable" });
        expect(calls[2].body).toContain("same bytes");
    });
}

test("identical conflict names expose exact replacement targets", async ({ page }) => {
    const calls = await intercept(page, (upload, count) => {
        const result = conflict(upload.requestUID);
        result.conflicts = result.conflicts.map((item) => ({ ...item, name: "same.txt", can_replace: true }));
        return count === 1 ? { status: 409, json: result } : { status: 201, json: success(upload.requestUID) };
    });
    await page.goto(path);
    await start(page, "editor");
    await expect(page.getByRole("button", { name: "Replace same.txt (replaceable)", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Replace same.txt (protected)", exact: true }).click();
    await expect.poll(() => calls.length).toBe(2);
    expect(calls[1].replacement).toBe("protected");
});
