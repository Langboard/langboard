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
const response = (items: ReturnType<typeof item>[] = [], next: string | null = null) => ({
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
        return r.fulfill({ json: response([item("report"), item("archive", "inbox")]) });
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
    await expect(page.getByRole("status")).toContainText("not supported");
    expect(requests.some((url) => url.includes("view=links"))).toBe(false);
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
