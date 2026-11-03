import { test, expect } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/cloud/BoardCloud.fixture.html";
const item = (id: string, folder = "documents", board = "cloud-board") => ({
    id,
    attachment_uid: id,
    name: `${id}.pdf`,
    url: `/storage/${id}.pdf`,
    media_type: "application/pdf",
    kind: "file",
    folder_id: folder,
    created_at: "2026-10-20T10:00:00",
    updated_at: "2026-10-20T10:00:00",
    source: { project_uid: board, card_uid: "source-card", title: "Source card", board_url: `/board/${board}`, url: `/board/${board}/source-card` },
});
const response = (items: unknown[] = [], next: string | null = null) => ({
    items,
    folders: ["Inbox", "Documents", "Media"].map((name) => ({ id: name.toLowerCase(), name })),
    total: null,
    total_is_exact: false,
    recent_limit: 20,
    mime_type_source: "filename_extension",
    has_more: !!next,
    next_cursor: next,
    capabilities: { hyperlinks: false, upload: false, replace: false },
});
test("page and panel, folders, list/grid, download, preview and source", async ({ page }) => {
    const requests: string[] = [];
    await page.route("**/board/*/cloud?**", (r) => {
        requests.push(r.request().url());
        return r.fulfill({
            json: new URL(r.request().url()).searchParams.get("view") === "links" ? response() : response([item("report"), item("archive", "inbox")]),
        });
    });
    await page.route("**/storage/report.pdf", (r) => r.fulfill({ contentType: "application/pdf", body: "%PDF fixture" }));
    await page.goto(fixture);
    await expect(page.getByRole("list", { name: "Attachments" }).getByText("report.pdf", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Open cloud", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/cloud");
    await page.getByRole("button", { name: "Toggle panel" }).click();
    await expect(page.getByRole("button", { name: "Open cloud", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Documents", exact: true }).click();
    const attachments = page.getByRole("list", { name: "Attachments" });
    await expect(attachments.getByText("archive.pdf")).toHaveCount(0);
    await page.getByRole("button", { name: "Grid", exact: true }).click();
    await expect(attachments).toHaveAttribute("data-layout", "grid");
    const downloaded = page.waitForEvent("download");
    await attachments.getByRole("button", { name: "Download", exact: true }).click();
    expect((await downloaded).suggestedFilename()).toBe("report.pdf");
    await attachments.getByRole("button", { name: "report.pdf", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await attachments.getByRole("button", { name: "Source: Source card", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/source-card");
    await page.getByRole("button", { name: "Links", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("No links in the scanned sources");
    expect(requests.some((url) => url.includes("view=links"))).toBe(true);
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toHaveCount(0);
});
test("empty filtered continuation keeps opaque cursor and category scope", async ({ page }) => {
    const requests: URL[] = [];
    await page.route("**/board/*/cloud?**", (r) => {
        const url = new URL(r.request().url());
        requests.push(url);
        return r.fulfill({
            json:
                url.searchParams.get("view") === "files"
                    ? response(url.searchParams.has("cursor") ? [item("older")] : [], url.searchParams.has("cursor") ? null : "opaque+/=cursor")
                    : response(),
        });
    });
    await page.goto(fixture);
    await page.getByRole("button", { name: "Files", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Continue loading");
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    await expect(page.getByRole("list").getByText("older.pdf", { exact: true })).toBeVisible();
    expect(requests.find((url) => url.searchParams.has("cursor"))?.searchParams.get("cursor")).toBe("opaque+/=cursor");
    await page.getByLabel("Categories").getByRole("button", { name: "Media", exact: true }).click();
    await expect(page.getByRole("list").getByText("older.pdf", { exact: true })).toHaveCount(0);
    await expect.poll(() => requests.at(-1)?.searchParams.get("view")).toBe("media");
    expect(requests.at(-1)?.searchParams.get("cursor")).toBeNull();
});
test("loading, denied read, retry, actor and board isolation", async ({ page }) => {
    let calls = 0;
    let release: () => void = () => {};
    const delayed = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/board/*/cloud?**", async (r) => {
        calls++;
        if (calls === 1) {
            await delayed;
            return r.fulfill({ status: 404, json: {} });
        }
        const board = new URL(r.request().url()).pathname.split("/")[2];
        return r.fulfill({ json: response([item(`read-${calls}`, "documents", board)]) });
    });
    await page.goto(fixture);
    await expect(page.getByRole("status")).toContainText("Loading");
    release();
    await expect(page.getByRole("alert")).toContainText("Could not load");
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("list").getByText("read-2.pdf", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Switch actor" }).click();
    await expect(page.getByRole("list").getByText("read-2.pdf", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("list").getByText("read-3.pdf", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Switch board" }).click();
    await expect(page.getByRole("list").getByText("read-3.pdf", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("list").getByText("read-4.pdf", { exact: true })).toBeVisible();
});
test("Korean native menu name", async ({ page }) => {
    await page.route("**/board/*/cloud?**", (r) => r.fulfill({ json: response() }));
    await page.goto(`${fixture}?lang=ko-KR`);
    await expect(page.getByRole("heading", { name: "클라우드", exact: true })).toBeVisible();
});
test("media thumbnails, wiki source and recent use native views", async ({ page }) => {
    const views: string[] = [];
    await page.route("**/board/*/cloud?**", (r) => {
        views.push(new URL(r.request().url()).searchParams.get("view")!);
        const media = {
            ...item("photo", "media"),
            name: "photo.png",
            url: "/storage/photo.png",
            kind: "media",
            media_type: "image/png",
            source: {
                project_uid: "cloud-board",
                wiki_uid: "wiki-source",
                title: "Source wiki",
                board_url: "/board/cloud-board",
                url: "/board/cloud-board/wiki/wiki-source",
            },
        };
        return r.fulfill({ json: { ...response(), items: [media] } });
    });
    await page.route("**/storage/photo.png", (r) =>
        r.fulfill({
            contentType: "image/png",
            body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=", "base64"),
        })
    );
    await page.goto(fixture);
    await page.getByLabel("Categories").getByRole("button", { name: "Media", exact: true }).click();
    await expect.poll(() => views.at(-1)).toBe("media");
    await page.getByRole("button", { name: "Grid", exact: true }).click();
    await expect(page.getByRole("list").getByRole("img", { name: "photo.png" })).toBeVisible();
    await page.getByRole("list").getByRole("button", { name: "Source: Source wiki", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/wiki/wiki-source");
    await page.getByRole("button", { name: "Recent", exact: true }).click();
    await expect.poll(() => views.at(-1)).toBe("recent");
});
test("failed continuation hides cached files and retry revalidates the first page", async ({ page }) => {
    let denied = false;
    const cursors: (string | null)[] = [];
    await page.route("**/board/*/cloud?**", (r) => {
        const cursor = new URL(r.request().url()).searchParams.get("cursor");
        cursors.push(cursor);
        if (cursor) {
            denied = true;
            return r.fulfill({ status: 404, json: {} });
        }
        return r.fulfill({ json: denied ? response([item("current")]) : response([item("revoked")], "next") });
    });
    await page.goto(fixture);
    const files = page.getByRole("list", { name: "Attachments" });
    await expect(files.getByText("revoked.pdf", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(files.getByText("revoked.pdf", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Load more", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(files.getByText("current.pdf", { exact: true })).toBeVisible();
    await expect(files.getByText("revoked.pdf", { exact: true })).toHaveCount(0);
    expect(cursors).toEqual([null, "next", null]);
});

const link = (id: string, wiki = false) => ({
    ...item(id, "links"),
    kind: "link",
    name: `Saved ${id}`,
    url: `https://external.invalid/${id}`,
    media_type: "text/uri-list",
    source: wiki
        ? {
              project_uid: "cloud-board",
              wiki_uid: "source-wiki",
              title: "Source wiki",
              board_url: "/board/cloud-board",
              url: "/board/cloud-board/wiki/source-wiki",
          }
        : item(id).source,
});
test("saved links use native tree, list, grid and card/wiki navigation without remote fetch", async ({ page }) => {
    const external: string[] = [];
    await page.route("https://external.invalid/**", (route) => {
        external.push(route.request().url());
        return route.abort();
    });
    await page.route("**/board/*/cloud?**", (route) =>
        route.fulfill({
            json: {
                ...response(),
                items: new URL(route.request().url()).searchParams.get("view") === "links" ? [link("card"), link("wiki", true)] : [],
                capabilities: { hyperlinks: true, upload: false, replace: false, all_recent: "attachments_only" },
            },
        })
    );
    await page.goto(fixture);
    await page.getByLabel("Categories").getByRole("button", { name: "Links", exact: true }).click();
    const links = page.getByRole("list", { name: "Links" });
    await expect(links.getByRole("link", { name: "Saved card", exact: true })).toHaveAttribute("href", "https://external.invalid/card");
    await expect(links.getByRole("link", { name: "Saved card", exact: true })).toHaveAttribute("rel", "noopener noreferrer");
    await expect(links.getByRole("link", { name: "Saved card", exact: true })).toHaveAttribute("target", "_blank");
    await expect(page.getByText("no remote content is fetched", { exact: false })).toBeVisible();
    await page.getByLabel("Folders").getByRole("button", { name: "Links", exact: true }).click();
    await expect(page.getByLabel("Folders").getByRole("button", { name: "Saved wiki", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Grid", exact: true }).click();
    await expect(links).toHaveAttribute("data-layout", "grid");
    await links.getByRole("button", { name: "Source: Source wiki", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/wiki/source-wiki");
    await page.getByRole("button", { name: "List", exact: true }).click();
    await links.getByRole("button", { name: "Source: Source card", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/source-card");
    expect(external).toEqual([]);
    await expect(links.getByRole("button", { name: "Download", exact: true })).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("link continuation preserves cursor, deduplicates loaded items and exposes oversized source", async ({ page }) => {
    const requests: URL[] = [];
    await page.route("**/board/*/cloud?**", (route) => {
        const url = new URL(route.request().url());
        requests.push(url);
        if (url.searchParams.get("view") !== "links") return route.fulfill({ json: response() });
        const next = url.searchParams.has("cursor");
        return route.fulfill({
            json: {
                ...response([], next ? null : "links+/=opaque"),
                items: next ? [link("first"), link("second", true)] : [link("first")],
                limitations: next ? [{ reason: "source_too_large", source: { ...item("large").source, title: "Large source" } }] : [],
                limitation_count: next ? 1 : 0,
            },
        });
    });
    await page.goto(fixture);
    await page.getByLabel("Categories").getByRole("button", { name: "Links", exact: true }).click();
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    const links = page.getByRole("list", { name: "Links" });
    await expect(links.getByRole("link")).toHaveCount(2);
    await expect(page.getByRole("status")).toContainText("1 sources exceed the scan budget");
    await page.getByRole("button", { name: "Source: Large source", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/source-card");
    expect(requests.find((url) => url.searchParams.has("cursor"))?.searchParams.get("cursor")).toBe("links+/=opaque");
    await page.getByLabel("Categories").getByRole("button", { name: "Recent", exact: true }).click();
    await expect(links).toHaveCount(0);
    expect(requests.at(-1)?.searchParams.get("cursor")).toBeNull();
});
for (const url of ["javascript:alert(1)", "https://user:secret@external.invalid/private"]) {
    test(`unsafe projected link fails closed: ${url}`, async ({ page }) => {
        await page.route("**/board/*/cloud?**", (route) => route.fulfill({ json: { ...response(), items: [{ ...link("unsafe"), url }] } }));
        await page.goto(fixture);
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByRole("link")).toHaveCount(0);
    });
}

// All upload tests below exercise native Chrome with mocked HTTP, not authenticated canary evidence.
const uploadRoute = "**/board/*/cloud/attachment";
const field = (body: string, name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]+)`))?.[1];
const parseUpload = (body: string) => ({
    uid: field(body, "request_uid"),
    folder: field(body, "folder_id"),
    action: field(body, "duplicate_action"),
    token: field(body, "confirmation_token"),
    target: field(body, "replacement_uid"),
    filename: body.match(/filename="([^"]+)"/)?.[1],
});
const conflictResponse = (uid: string, token = "first-token") => ({
    confirmation_required: true,
    confirmation_token: token,
    request_uid: uid,
    conflicts: [
        { uid: "direct-asset", name: "report.pdf", same_name: true, same_content: true, can_replace: true },
        { uid: "forbidden-asset", name: "protected.pdf", same_name: false, same_content: true, can_replace: false },
    ],
});
const receipt = (upload: ReturnType<typeof parseUpload>, extra = {}) => ({
    uid: "new-project-asset",
    request_uid: upload.uid,
    project_uid: "cloud-board",
    folder_id: upload.folder,
    name: upload.filename,
    url: "/storage/uploaded",
    created_at: "2026-10-22T10:00:00Z",
    updated_at: "2026-10-22T10:00:00Z",
    replayed: false,
    ...extra,
});
async function uploadCatalog(page: import("@playwright/test").Page) {
    let reads = 0;
    await page.route("**/board/*/cloud?**", (route) => {
        reads++;
        return route.fulfill({ json: { ...response(), capabilities: { upload: true, replace: true, hyperlinks: true } } });
    });
    return () => reads;
}
const files = [
    { name: "report.pdf", mimeType: "application/pdf", buffer: Buffer.from("exact report bytes") },
    { name: "second.txt", mimeType: "text/plain", buffer: Buffer.from("second bytes") },
];

test("serialized multi-file upload uses selected default folder and isolated UUIDs", async ({ page }) => {
    const reads = await uploadCatalog(page);
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, async (route) => {
        const upload = parseUpload(route.request().postData()!);
        calls.push(upload);
        await route.fulfill({ status: 201, json: receipt(upload) });
    });
    await page.goto(fixture);
    await page.getByRole("button", { name: "Documents", exact: true }).click();
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files);
    await expect.poll(() => calls.length).toBe(2);
    expect(calls.map((call) => call.filename)).toEqual(["report.pdf", "second.txt"]);
    expect(calls.map((call) => call.folder)).toEqual(["documents", "documents"]);
    expect(calls[0].uid).toMatch(/^[a-f0-9-]{36}$/);
    expect(calls[1].uid).not.toBe(calls[0].uid);
    await expect.poll(reads).toBeGreaterThan(1);
});

test("stale replace requires fresh decision, ambiguous failure replays exact decision and bytes", async ({ page }) => {
    await uploadCatalog(page);
    const bodies: string[] = [];
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, async (route) => {
        const body = route.request().postData()!;
        bodies.push(body);
        const upload = parseUpload(body);
        calls.push(upload);
        if (calls.length <= 2)
            return route.fulfill({ status: 409, json: conflictResponse(upload.uid!, calls.length === 1 ? "first-token" : "fresh-token") });
        if (calls.length === 3) return route.abort("failed");
        return route.fulfill({ json: receipt(upload, { replayed: true }) });
    });
    await page.goto(fixture);
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files[0]);
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: /Replace.*protected/ })).toHaveCount(0);
    await dialog.getByRole("button", { name: /Replace.*report/ }).click();
    await expect(dialog).toBeVisible();
    expect(calls).toHaveLength(2);
    await dialog.getByRole("button", { name: "Add separate attachment", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Could not confirm upload");
    await page.getByRole("button", { name: "Retry upload", exact: true }).click();
    await expect.poll(() => calls.length).toBe(4);
    expect(calls.map((call) => call.uid)).toEqual(Array(4).fill(calls[0].uid));
    expect(calls[1]).toMatchObject({ action: "replace", token: "first-token", target: "direct-asset", folder: "inbox" });
    expect(calls[2]).toEqual(calls[3]);
    expect(calls[3]).toMatchObject({ action: "add", token: "fresh-token", target: undefined });
    expect(bodies[2].split("\r\n").filter((line) => !line.startsWith("------WebKitFormBoundary"))).toEqual(
        bodies[3].split("\r\n").filter((line) => !line.startsWith("------WebKitFormBoundary"))
    );
});

test("explicit cancel posts cancellation then proceeds with next file", async ({ page }) => {
    await uploadCatalog(page);
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, async (route) => {
        const upload = parseUpload(route.request().postData()!);
        calls.push(upload);
        if (calls.length === 1) return route.fulfill({ status: 409, json: conflictResponse(upload.uid!) });
        return route.fulfill({ json: upload.action === "cancel" ? { cancelled: true, request_uid: upload.uid } : receipt(upload) });
    });
    await page.goto(fixture);
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
    await expect.poll(() => calls.length).toBe(3);
    expect(calls[1]).toMatchObject({ uid: calls[0].uid, action: "cancel", token: "first-token" });
    expect(calls[2].filename).toBe("second.txt");
});

for (const change of ["Switch actor", "Switch board"]) {
    test(`${change} dismisses pending decisions without sending stale actions`, async ({ page }) => {
        await uploadCatalog(page);
        let calls = 0;
        await page.route(uploadRoute, (route) => {
            calls++;
            const upload = parseUpload(route.request().postData()!);
            return route.fulfill({ status: 409, json: conflictResponse(upload.uid!) });
        });
        await page.goto(fixture);
        await page.getByLabel("Upload files", { exact: true }).setInputFiles(files);
        await expect(page.getByRole("dialog")).toBeVisible();
        // The native modal intentionally traps interaction; change identity through the fixture's native handler.
        await page.getByRole("button", { name: change, exact: true, includeHidden: true }).evaluate((button: HTMLButtonElement) => button.click());
        await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(calls).toBe(1);
    });
}

test.describe("mobile upload", () => {
    test.use({ hasTouch: true, isMobile: true });
    test("mobile touch upload and Escape cancellation preserve Cloud layout", async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await uploadCatalog(page);
        const calls: ReturnType<typeof parseUpload>[] = [];
        await page.route(uploadRoute, (route) => {
            const upload = parseUpload(route.request().postData()!);
            calls.push(upload);
            return route.fulfill({
                status: upload.action ? 200 : 409,
                json: upload.action ? { cancelled: true, request_uid: upload.uid } : conflictResponse(upload.uid!),
            });
        });
        await page.goto(fixture);
        const chooser = page.waitForEvent("filechooser");
        await page.getByRole("button", { name: "Upload", exact: true }).tap();
        await (await chooser).setFiles(files[0]);
        await expect(page.getByRole("dialog")).toBeVisible();
        await page.keyboard.press("Escape");
        await expect.poll(() => calls.length).toBe(2);
        expect(calls[1].action).toBe("cancel");
        await page.getByRole("button", { name: "Grid", exact: true }).click();
        await expect(page.getByRole("list", { name: "Attachments" })).toHaveAttribute("data-layout", "grid");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
});

test("wrong-project receipt fails closed without invalidation or queue continuation", async ({ page }) => {
    const reads = await uploadCatalog(page);
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, (route) => {
        const upload = parseUpload(route.request().postData()!);
        calls.push(upload);
        return route.fulfill({ json: receipt(upload, { project_uid: "foreign-board" }) });
    });
    await page.goto(fixture);
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    const before = reads();
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files);
    await expect(page.getByRole("alert")).toContainText("Could not confirm upload");
    expect(calls).toHaveLength(1);
    expect(reads()).toBe(before);
    await page.getByRole("alert").getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
});

test("failed cancel retries same cancellation, revoked upload never advances the queue", async ({ page }) => {
    const reads = await uploadCatalog(page);
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, (route) => {
        const upload = parseUpload(route.request().postData()!);
        calls.push(upload);
        if (calls.length === 1) return route.fulfill({ status: 409, json: conflictResponse(upload.uid!) });
        if (calls.length === 2) return route.fulfill({ status: 503, json: {} });
        if (upload.action === "cancel") return route.fulfill({ json: { cancelled: true, request_uid: upload.uid, replayed: true } });
        return route.fulfill({ status: 403, json: {} });
    });
    await page.goto(fixture);
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    const before = reads();
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Retry upload", exact: true }).click();
    await expect.poll(() => calls.length).toBe(4);
    expect(calls[1]).toEqual(calls[2]);
    await expect(page.getByRole("alert")).toContainText("second.txt");
    expect(reads()).toBe(before);
});

for (const change of ["Switch actor", "Switch board"]) {
    test(`${change} rejects late upload completion and remaining queue`, async ({ page }) => {
        await uploadCatalog(page);
        let release: () => void = () => {};
        const delayed = new Promise<void>((resolve) => {
            release = resolve;
        });
        let calls = 0;
        await page.route(uploadRoute, async (route) => {
            calls++;
            const upload = parseUpload(route.request().postData()!);
            await delayed;
            await route.fulfill({ status: 201, json: receipt(upload) }).catch(() => {});
        });
        await page.goto(fixture);
        await page.getByLabel("Upload files", { exact: true }).setInputFiles(files);
        await expect.poll(() => calls).toBe(1);
        await page.getByRole("button", { name: change, exact: true }).click();
        release();
        await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeEnabled();
        expect(calls).toBe(1);
        await expect(page.getByRole("alert")).toHaveCount(0);
    });
}

test("project source navigates to Cloud without synthetic card or wiki", async ({ page }) => {
    await page.route("**/board/*/cloud?**", (route) =>
        route.fulfill({
            json: response([
                {
                    ...item("direct-project"),
                    source: {
                        kind: "project",
                        project_uid: "cloud-board",
                        title: "Project files",
                        board_url: "/board/cloud-board",
                        url: "/board/cloud-board",
                    },
                },
            ]),
        })
    );
    await page.goto(fixture);
    await page.getByRole("list", { name: "Attachments" }).getByRole("button", { name: "Source: Project files", exact: true }).click();
    await expect(page.locator("output")).toHaveText("/board/cloud-board/cloud");
});

test("successful direct replacement preserves target UID and refreshes only Cloud", async ({ page }) => {
    const reads = await uploadCatalog(page);
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, (route) => {
        const upload = parseUpload(route.request().postData()!);
        calls.push(upload);
        if (!upload.action) return route.fulfill({ status: 409, json: conflictResponse(upload.uid!) });
        return route.fulfill({ json: receipt(upload, { uid: upload.target }) });
    });
    await page.goto(fixture);
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files[0]);
    await page
        .getByRole("dialog")
        .getByRole("button", { name: /Replace.*report/ })
        .click();
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeEnabled();
    expect(calls[1]).toMatchObject({ action: "replace", target: "direct-asset", uid: calls[0].uid });
    await expect.poll(reads).toBeGreaterThan(1);
});

test("ambiguous initial upload retries identical payload with same UUID", async ({ page }) => {
    await uploadCatalog(page);
    const calls: ReturnType<typeof parseUpload>[] = [];
    await page.route(uploadRoute, (route) => {
        const upload = parseUpload(route.request().postData()!);
        calls.push(upload);
        return calls.length === 1 ? route.abort("failed") : route.fulfill({ json: receipt(upload, { replayed: true }) });
    });
    await page.goto(fixture);
    await page.getByLabel("Upload files", { exact: true }).setInputFiles(files[0]);
    await page.getByRole("button", { name: "Retry upload", exact: true }).click();
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeEnabled();
    expect(calls).toHaveLength(2);
    expect(calls[0]).toEqual(calls[1]);
    expect(calls[1].action).toBeUndefined();
});

// Document responses are labeled HTTP fixtures; these tests do not prove live extraction or inference.
const nativeDocumentItem = () => ({
    ...item("native-document"),
    source: {
        kind: "project",
        project_uid: "cloud-board",
        title: "Cloud fixture",
        board_url: "/board/cloud-board/cloud",
        url: "/board/cloud-board/cloud",
    },
});
const nativeDocumentSource = {
    source_type: "project_attachment",
    project_uid: "cloud-board",
    attachment_uid: "native-document",
    name: "native-document.pdf",
    url: "/storage/native-document.pdf",
    board_url: "/board/cloud-board/cloud",
};
const documentFixturePage = (extra = {}) => ({
    source: nativeDocumentSource,
    generation: "fixture-generation",
    status: "indexed",
    text: "Private native fixture text",
    total_chars: 8000,
    next_offset: 4000,
    keywords: { en: ["fixture"] },
    embedding_status: "indexed",
    ...extra,
});
const documentPath = "**/board/cloud-board/cloud/attachment/native-document/document";
const wikiDocumentSource = {
    ...nativeDocumentSource,
    source_type: "wiki_attachment",
    wiki_uid: "native-wiki",
    board_url: "/board/cloud-board/wiki/native-wiki",
};
const wikiDocumentPath = "**/board/cloud-board/cloud/wiki/native-wiki/attachment/native-document/document";
async function wikiDocumentCatalog(page: import("@playwright/test").Page) {
    const source = { ...nativeDocumentItem().source, kind: "wiki", wiki_uid: "native-wiki", url: wikiDocumentSource.board_url };
    await page.route("**/board/*/cloud?**", (route) => route.fulfill({ json: response([{ ...nativeDocumentItem(), source }]) }));
    await page.goto(fixture);
    await page.getByRole("button", { name: "Document", exact: true }).click();
}

test("wiki document uses its own process, read and search routes with native citations", async ({ page }) => {
    const calls: string[] = [];
    await page.route(`${wikiDocumentPath}?**`, (route) => {
        calls.push("read");
        return route.fulfill({ json: documentFixturePage({ source: wikiDocumentSource }) });
    });
    await page.route(`${wikiDocumentPath}/process`, (route) => {
        calls.push("process");
        return route.fulfill({ json: { attachment_uid: "native-document", generation: "wiki-generation", status: "pending" } });
    });
    await page.route(`${wikiDocumentPath}/search`, (route) => {
        calls.push("search");
        return route.fulfill({
            json: {
                source: wikiDocumentSource,
                generation: "wiki-generation",
                matches: [{ chunk_id: "wiki-chunk", content: "Wiki semantic excerpt", source: wikiDocumentSource, pages: [1], page: 1 }],
            },
        });
    });
    await wikiDocumentCatalog(page);
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toContainText("Private native fixture text");
    await page.getByLabel("Document query", { exact: true }).fill("wiki query");
    await page.getByRole("button", { name: "Search document", exact: true }).click();
    await expect(page.getByLabel("Document matches")).toContainText("Wiki semantic excerpt");
    await expect(page.getByRole("link", { name: "Source: native-document.pdf" })).toHaveAttribute("href", wikiDocumentSource.url);
    await page.getByRole("button", { name: "Process document", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Processing queued");
    expect(calls).toEqual(["read", "search", "process"]);
});

for (const wrongSource of [nativeDocumentSource, { ...wikiDocumentSource, wiki_uid: "another-wiki" }]) {
    test(`wiki document rejects mismatched owner ${wrongSource.source_type}-${"wiki_uid" in wrongSource ? wrongSource.wiki_uid : "none"}`, async ({
        page,
    }) => {
        await page.route(`${wikiDocumentPath}?**`, (route) => route.fulfill({ json: documentFixturePage({ source: wrongSource }) }));
        await wikiDocumentCatalog(page);
        await page.getByRole("button", { name: "Read document", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Document unavailable");
        await expect(page.getByText("Private native fixture text", { exact: true })).toHaveCount(0);
    });
}

async function documentCatalog(page: import("@playwright/test").Page, next: string | null = null) {
    await page.route("**/board/*/cloud?**", (route) => route.fulfill({ json: response([nativeDocumentItem(), item("legacy-card")], next) }));
    await page.goto(fixture);
    await expect(page.getByRole("list", { name: "Attachments" }).getByRole("button", { name: "Document", exact: true })).toHaveCount(1);
    await page.getByRole("button", { name: "Document", exact: true }).click();
}

for (const status of ["pending", "processing", "failed"] as const) {
    for (const replaced of [false, true]) {
        test(`retained published document remains readable during ${status} with ${replaced ? "previous" : "current"} bytes`, async ({ page }) => {
            const source = replaced
                ? { ...nativeDocumentSource, name: "previous.pdf", url: "/file/local/published/previous.pdf" }
                : nativeDocumentSource;
            await page.route(`${documentPath}?**`, (route) => route.fulfill({ json: documentFixturePage({ source, status, next_offset: null }) }));
            await documentCatalog(page);
            await page.getByRole("button", { name: "Read document", exact: true }).click();
            await expect(page.getByLabel("Document text", { exact: true })).toHaveText("Private native fixture text");
            await expect(page.getByRole("link", { name: `Source: ${source.name}`, exact: true })).toHaveAttribute("href", source.url);
            await expect(page.getByRole("status")).toContainText(
                status === "pending" ? "Processing queued" : status === "processing" ? "Processing document" : "Document processing failed"
            );
            await expect(page.getByRole("link", { name: "Original file", exact: true })).toHaveAttribute("href", nativeDocumentSource.url);
        });
    }
}

for (const wiki of [false, true]) {
    test(`retained ${wiki ? "wiki" : "native"} semantic results cite their published bytes`, async ({ page }) => {
        const source = {
            ...(wiki ? wikiDocumentSource : nativeDocumentSource),
            name: "published-vector.pdf",
            url: "/file/local/published/published-vector.pdf",
        };
        await page.route(`${wiki ? wikiDocumentPath : documentPath}/search`, (route) =>
            route.fulfill({
                json: {
                    source,
                    generation: "published-vector-generation",
                    matches: [{ chunk_id: "published-chunk", content: "Retained semantic excerpt", source, pages: [2], page: 2 }],
                },
            })
        );
        if (wiki) await wikiDocumentCatalog(page);
        else await documentCatalog(page);
        await page.getByLabel("Document query", { exact: true }).fill("retained query");
        await page.getByRole("button", { name: "Search document", exact: true }).click();
        await expect(page.getByLabel("Document matches")).toContainText("Retained semantic excerpt");
        await expect(page.getByRole("link", { name: "Source: published-vector.pdf", exact: true })).toHaveAttribute("href", source.url);
    });
}

for (const source of [
    { ...nativeDocumentSource, project_uid: "foreign-board", url: "/file/local/published/foreign.pdf" },
    { ...nativeDocumentSource, url: "https://foreign.example/published.pdf" },
    { ...nativeDocumentSource, url: "/file/../private.pdf" },
]) {
    test(`retained document rejects foreign owner or locator ${source.project_uid} ${source.url}`, async ({ page }) => {
        await page.route(`${documentPath}?**`, (route) => route.fulfill({ json: documentFixturePage({ source, status: "failed" }) }));
        await documentCatalog(page);
        await page.getByRole("button", { name: "Read document", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Document unavailable");
        await expect(page.getByLabel("Document text", { exact: true })).toHaveCount(0);
    });
}

test("native document explicit read, bounded pages, process receipt and semantic citations", async ({ page }) => {
    const reads: URL[] = [];
    const processes: string[] = [];
    const searches: unknown[] = [];
    await page.route(`${documentPath}?**`, (route) => {
        const url = new URL(route.request().url());
        reads.push(url);
        return route.fulfill({
            json: documentFixturePage(url.searchParams.get("offset") === "4000" ? { text: "Second fixture page", next_offset: null } : {}),
        });
    });
    await page.route(`${documentPath}/process`, (route) => {
        processes.push(route.request().postData() ?? "");
        return route.fulfill({ json: { attachment_uid: "native-document", generation: "queued-fixture", status: "pending" } });
    });
    await page.route(`${documentPath}/search`, (route) => {
        searches.push(route.request().postDataJSON());
        return route.fulfill({
            json: {
                source: nativeDocumentSource,
                generation: "fixture-generation",
                matches: [
                    { chunk_id: "fixture-chunk", content: "Native semantic fixture excerpt", source: nativeDocumentSource, pages: [2], page: 2 },
                ],
            },
        });
    });
    await documentCatalog(page);
    expect(reads).toHaveLength(0);
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toHaveText("Private native fixture text");
    expect(reads[0].searchParams.get("limit")).toBe("4000");
    await page.getByRole("button", { name: "Next document page", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toHaveText("Second fixture page");
    expect(reads[1].searchParams.get("offset")).toBe("4000");
    await page.getByLabel("Document query", { exact: true }).fill("  native query  ");
    await page.getByRole("button", { name: "Search document", exact: true }).click();
    await expect(page.getByLabel("Document matches")).toContainText("Native semantic fixture excerpt");
    await expect(page.getByRole("link", { name: "Source: native-document.pdf" })).toHaveAttribute("href", nativeDocumentSource.url);
    expect(searches).toEqual([{ query: "native query", max_tokens: 2000 }]);
    await page.getByRole("button", { name: "Process document", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Processing queued");
    await expect(page.getByText("Native semantic fixture excerpt", { exact: true })).toHaveCount(0);
    expect(processes).toEqual([""]);
});

test("document failed reads clear private results and search unavailable is not success", async ({ page }) => {
    let reads = 0;
    await page.route(`${documentPath}?**`, (route) => route.fulfill(++reads === 1 ? { json: documentFixturePage() } : { status: 403, json: {} }));
    await page.route(`${documentPath}/search`, (route) => route.fulfill({ status: 400, json: { detail: "vectors unavailable" } }));
    await documentCatalog(page);
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Document unavailable");
    await expect(page.getByLabel("Document text", { exact: true })).toHaveCount(0);
    await page.getByLabel("Document query", { exact: true }).fill("query");
    await page.getByRole("button", { name: "Search document", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Document unavailable");
    await expect(page.getByLabel("Document matches")).toHaveCount(0);
});

for (const change of ["Switch actor", "Switch board"] as const) {
    test(`document late read ignored on ${change}`, async ({ page }) => {
        let release: () => void = () => {};
        const delayed = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route(`${documentPath}?**`, async (route) => {
            await delayed;
            await route.fulfill({ json: documentFixturePage() }).catch(() => {});
        });
        await documentCatalog(page);
        await page.getByRole("button", { name: "Read document", exact: true }).click();
        await expect(page.getByRole("status")).toContainText("Loading document");
        await page.getByRole("button", { name: change, exact: true, includeHidden: true }).evaluate((button: HTMLButtonElement) => button.click());
        await expect(page.getByRole("dialog")).toHaveCount(0);
        release();
        await expect(page.getByText("Private native fixture text", { exact: true })).toHaveCount(0);
    });
}

test("failed current Cloud authorization refresh unmounts private document", async ({ page }) => {
    await page.route(`${documentPath}?**`, (route) => route.fulfill({ json: documentFixturePage() }));
    await documentCatalog(page, "next");
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toBeVisible();
    await page.route("**/board/*/cloud?**", (route) => route.fulfill({ status: 403, json: {} }));
    // Native modal traps pointer interaction; invoke the fixture's actual continuation button.
    await page.getByRole("button", { name: "Load more", exact: true, includeHidden: true }).evaluate((button: HTMLButtonElement) => button.click());
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByText("Private native fixture text", { exact: true })).toHaveCount(0);
});

test("changed document generation never combines pages", async ({ page }) => {
    await page.route(`${documentPath}?**`, (route) =>
        route.fulfill({
            json: documentFixturePage(
                new URL(route.request().url()).searchParams.get("offset") === "4000"
                    ? { generation: "changed", text: "Wrong generation content" }
                    : {}
            ),
        })
    );
    await documentCatalog(page);
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next document page", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Document unavailable");
    await expect(page.getByText("Wrong generation content", { exact: true })).toHaveCount(0);
});

test("native document pagination accepts 4000 Unicode codepoints", async ({ page }) => {
    const text = "😀".repeat(4000);
    await page.route(`${documentPath}?**`, (route) => route.fulfill({ json: documentFixturePage({ text }) }));
    await documentCatalog(page);
    await page.getByRole("button", { name: "Read document", exact: true }).click();
    await expect(page.getByLabel("Document text", { exact: true })).toHaveText(text);
    await expect(page.getByRole("button", { name: "Next document page", exact: true })).toBeVisible();
});

for (const size of [500, 5000]) {
    test(`large cloud ${size} keeps list/grid bounded and reaches late files and links`, async ({ page }) => {
        await page.route("**/board/*/cloud?**", (route) => {
            const links = new URL(route.request().url()).searchParams.get("view") === "links";
            return route.fulfill({
                json: response(
                    Array.from({ length: size }, (_, index) =>
                        links ? link(`entry-${index}`) : item(`entry-${index}`, index % 2 ? "inbox" : "documents")
                    )
                ),
            });
        });
        await page.goto(fixture);
        for (const kind of ["Attachments", "Links"] as const) {
            if (kind === "Links") await page.getByLabel("Categories").getByRole("button", { name: "Links", exact: true }).click();
            const collection = page.getByRole("list", { name: kind, exact: true });
            for (const layout of ["List", "Grid"] as const) {
                await page.getByRole("button", { name: layout, exact: true }).click();
                await expect(collection.getByRole("listitem").first()).toBeVisible();
                expect(await collection.getByRole("listitem").count()).toBeLessThan(100);
                const late =
                    kind === "Links"
                        ? collection.getByRole("link", { name: `Saved entry-${size - 1}`, exact: true })
                        : collection.getByRole("button", { name: `entry-${size - 1}.pdf`, exact: true });
                await expect
                    .poll(async () => {
                        await collection.evaluate((element) => {
                            element.parentElement!.scrollTop = element.parentElement!.scrollHeight;
                        });
                        return late.isVisible();
                    })
                    .toBe(true);
                expect(await collection.getByRole("listitem").count()).toBeLessThan(100);
                if (kind === "Attachments") {
                    await late.click();
                    await expect(page.getByRole("dialog")).toBeVisible();
                    await page.keyboard.press("Escape");
                }
            }
            if (kind === "Attachments") {
                await page.getByLabel("Folders").getByRole("button", { name: "Documents", exact: true }).click();
                await expect(collection.getByRole("button", { name: "entry-0.pdf", exact: true })).toBeVisible();
                await expect(collection.getByRole("button", { name: "entry-1.pdf", exact: true })).toHaveCount(0);
                await page.getByRole("button", { name: "List", exact: true }).click();
                await expect
                    .poll(async () => {
                        await collection.evaluate((element) => {
                            element.parentElement!.scrollTop = element.parentElement!.scrollHeight;
                        });
                        return collection.getByRole("button", { name: `entry-${size - 2}.pdf`, exact: true }).isVisible();
                    })
                    .toBe(true);
                expect(await collection.getByRole("listitem").count()).toBeLessThan(100);
            }
        }
    });
}

test("keyboard Tab and Shift Tab cross virtual row boundaries", async ({ page }) => {
    await page.route("**/board/*/cloud?**", (route) =>
        route.fulfill({ json: response(Array.from({ length: 500 }, (_, index) => item(`keyboard-${index}`))) })
    );
    await page.goto(fixture);
    const collection = page.getByRole("list", { name: "Attachments" });
    await collection.getByRole("button", { name: "keyboard-0.pdf", exact: true }).focus();
    for (let index = 0; index < 60; index++) await page.keyboard.press("Tab");
    await expect(collection.getByRole("button", { name: "keyboard-20.pdf", exact: true })).toBeFocused();
    expect(await collection.getByRole("listitem").count()).toBeLessThan(30);
    for (let index = 0; index < 60; index++) await page.keyboard.press("Shift+Tab");
    await expect(collection.getByRole("button", { name: "keyboard-0.pdf", exact: true })).toBeFocused();
});

test("compact panel and responsive grid retain bounded late-item access", async ({ page }) => {
    await page.route("**/board/*/cloud?**", (route) =>
        route.fulfill({ json: response(Array.from({ length: 5000 }, (_, index) => item(`responsive-${index}`))) })
    );
    await page.goto(`${fixture}?compact=true`);
    const collection = page.getByRole("list", { name: "Attachments" });
    await page.getByRole("button", { name: "Grid", exact: true }).click();
    for (const width of [1100, 390, 1100]) {
        await page.setViewportSize({ width, height: 1000 });
        await expect
            .poll(async () => {
                await collection.evaluate((element) => {
                    const viewport = element.parentElement!;
                    viewport.parentElement!.scrollTop = viewport.parentElement!.scrollHeight;
                    viewport.scrollTop = viewport.scrollHeight;
                });
                return collection.getByRole("button", { name: "responsive-4999.pdf", exact: true }).isVisible();
            })
            .toBe(true);
        await expect(collection.getByRole("button", { name: "responsive-4999.pdf", exact: true })).toBeInViewport();
        expect(await collection.getByRole("listitem").count()).toBeLessThan(100);
    }
});

for (const category of ["All", "Recent"]) {
    test(`mixed ${category} view includes file and link folders across pages`, async ({ page }) => {
        await page.route("**/board/*/cloud?**", (route) => {
            const query = new URL(route.request().url()).searchParams;
            const more = !!query.get("cursor");
            return route.fulfill({
                json: {
                    ...response(more ? [link("wiki", true)] : [item("report"), link("card")], more ? null : "mixed-next"),
                    folders: [...response().folders, { id: "links", name: "Links" }],
                    capabilities: { hyperlinks: true, upload: false, replace: false, all_recent: "attachments_and_links" },
                },
            });
        });
        await page.goto(fixture);
        await page.getByLabel("Categories").getByRole("button", { name: category, exact: true }).click();
        const list = page.getByRole("list", { name: "Cloud items", exact: true });
        await expect(list.getByText("report.pdf", { exact: true })).toBeVisible();
        await expect(list.getByRole("link", { name: "Saved card", exact: true })).toBeVisible();
        await expect(page.getByText("2 loaded items", { exact: true })).toBeVisible();
        await expect(page.getByText("Project files, readable card and wiki attachments, and saved links.", { exact: false })).toBeVisible();
        await page.getByRole("button", { name: "Load more", exact: true }).click();
        await expect(list.getByRole("link", { name: "Saved wiki", exact: true })).toBeVisible();
        await expect(page.getByText("3 loaded items", { exact: true })).toBeVisible();
        await page.getByLabel("Folders").getByRole("button", { name: "Links", exact: true }).click();
        await expect(list.getByText("report.pdf", { exact: true })).toHaveCount(0);
        await expect(list.getByRole("link")).toHaveCount(2);
        await page.getByRole("button", { name: "Grid", exact: true }).click();
        await expect(list).toHaveAttribute("data-layout", "grid");
    });
}

for (const kind of ["card", "wiki"] as const) {
    test(`source-aware ${kind} duplicate sends explicit native target and accepts its receipt`, async ({ page }) => {
        const reads = await uploadCatalog(page);
        const calls: (ReturnType<typeof parseUpload> & { source?: string })[] = [];
        const source = {
            kind,
            project_uid: "cloud-board",
            title: `Native ${kind} source`,
            url: `/board/cloud-board/${kind}/native-parent`,
            ...(kind === "card" ? { card_uid: "native-parent" } : { wiki_uid: "native-parent" }),
        };
        await page.route(uploadRoute, (route) => {
            const body = route.request().postData()!;
            const upload = { ...parseUpload(body), source: field(body, "replacement_source") };
            calls.push(upload);
            if (!upload.action)
                return route.fulfill({
                    status: 409,
                    json: {
                        ...conflictResponse(upload.uid!),
                        conflicts: [
                            { uid: "native-file", name: "report.pdf", same_name: true, same_content: true, can_replace: true, source },
                            {
                                uid: "direct-asset",
                                name: "report.pdf",
                                same_name: true,
                                same_content: true,
                                can_replace: true,
                                source: { kind: "project", project_uid: "cloud-board", title: "Board source", url: "/board/cloud-board" },
                            },
                        ],
                    },
                });
            return route.fulfill({
                json: receipt(upload, { uid: "native-file", source, ...(kind === "card" ? { card_uid: "native-parent" } : {}) }),
            });
        });
        await page.goto(fixture);
        await page
            .getByLabel("Upload files", { exact: true })
            .setInputFiles({ name: "report.pdf", mimeType: "application/pdf", buffer: Buffer.from("same bytes") });
        await expect(page.getByText(`Native ${kind} source`, { exact: false })).toBeVisible();
        const before = reads();
        await page.getByRole("button", { name: "Replace report.pdf (native-file)", exact: true }).click();
        await expect(page.getByRole("dialog")).toBeHidden();
        await expect.poll(() => calls.length).toBe(2);
        expect(calls[1].source).toBe(kind);
        expect(calls[1].target).toBe("native-file");
        await expect.poll(() => reads()).toBeGreaterThan(before);
        await expect(page.getByRole("alert")).toBeHidden();
    });
}
