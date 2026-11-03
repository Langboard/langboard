import { expect, test } from "@playwright/test";

for (const type of ["project_card", "project_wiki"] as const) {
    test(`closed ${type} mirror rereads source edits and revocation through BoardProvider`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        let title = "Authorized source title";
        let status = "available";
        const reads = { source: 0, destination: 0, detail: 0 };
        const writes: string[] = [];
        await page.route(
            (url) => url.pathname.startsWith("/board/"),
            async (route) => {
                const path = new URL(route.request().url()).pathname;
                if (route.request().method() !== "GET") writes.push(`${route.request().method()} ${path}`);
                const uid = path.split("/")[2] as "source" | "destination";
                if (path.endsWith("/cards")) {
                    reads[uid]++;
                    const now = new Date().toISOString();
                    return route.fulfill({
                        json: {
                            cards:
                                uid === "source"
                                    ? []
                                    : [
                                          {
                                              uid: "destination-mirror",
                                              project_uid: uid,
                                              project_column_uid: `${uid}-column`,
                                              project_column_name: "Destination column",
                                              created_at: now,
                                              updated_at: now,
                                              order: 0,
                                              source_type: type,
                                              source_uid: "source-card",
                                              title: "UNTRUSTED LOCAL TITLE",
                                              member_uids: [],
                                              labels: [],
                                              relationships: [],
                                              can_cross_board: false,
                                              linked_resource: {
                                                  type,
                                                  uid: "source-card",
                                                  project_uid: "source",
                                                  status,
                                                  ...(status === "available" ? { title, url: "/board/source/source-card" } : {}),
                                              },
                                          },
                                      ],
                            columns: [{ uid: `${uid}-column`, project_uid: uid, name: `${uid} column`, order: 0, created_at: now, updated_at: now }],
                            checklists: [],
                            global_relationships: [],
                        },
                    });
                }
                if (path.includes("/card/")) reads.detail++;
                return route.fulfill({ json: {} });
            }
        );
        await page.goto("/src/pages/BoardPage/components/card/mirror/CardMirror.fixture.html");
        const mirror = page.locator("#board-card-destination-mirror");
        await expect(mirror).toContainText(title, { timeout: 45_000 });
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(mirror.locator("input,textarea,[contenteditable=true],[role=checkbox]")).toHaveCount(0);
        const initial = await page.evaluate(async () => {
            const modelsURL = "/src/core/models/ProjectCard.ts";
            const model = (await import(modelsURL)).Model.getModel("destination-mirror");
            Object.assign(window, { closedMirror: model });
            for (const pane of document.querySelectorAll<HTMLElement>("[data-pane-scroll]")) pane.scrollTop = 75;
            return Array.from(document.querySelectorAll("[data-pane-filter]")).map((pane) => pane.textContent);
        });
        const notify = () =>
            page.evaluate(async () => {
                const emit = (
                    window as unknown as { emitNativeSummaryEvent: (topic: string, uid: string, event: string, data: unknown) => Promise<void> }
                ).emitNativeSummaryEvent;
                await emit("board", "destination", "board:card:linked-resource:changed:destination-mirror", { uid: "destination-mirror" });
            });
        const sourceReads = reads.source;
        title = "Source edit visible while detail remains closed";
        await notify();
        await expect(mirror).toContainText(title);
        status = "forbidden";
        await notify();
        await expect(mirror).toContainText(type === "project_wiki" ? "Restricted wiki" : "Source unavailable");
        const state = await page.evaluate(async () => {
            const url = "/src/core/models/ProjectCard.ts";
            const model = (await import(url)).Model.getModel("destination-mirror");
            return {
                same: model === (window as unknown as { closedMirror: unknown }).closedMirror,
                title: model?.title,
                resource: model?.linked_resource,
                filters: Array.from(document.querySelectorAll("[data-pane-filter]")).map((pane) => pane.textContent),
                scrolls: Array.from(document.querySelectorAll("[data-pane-scroll]")).map((pane) => pane.scrollTop),
            };
        });
        expect(state).toMatchObject({ same: true, title: "", resource: { status: "forbidden" }, filters: initial, scrolls: [75, 75] });
        expect(state.resource).not.toHaveProperty("title");
        expect(state.resource).not.toHaveProperty("content");
        expect(reads.source).toBe(sourceReads);
        expect(reads.destination).toBe(3);
        expect(reads.detail).toBe(0);
        expect(writes).toEqual([]);
        await page.getByRole("button", { name: "Toggle panes" }).click();
        const before = reads.destination;
        await notify();
        expect(reads.destination).toBe(before);
        await page.getByRole("button", { name: "Toggle panes" }).click();
        await expect(mirror).toBeVisible();
        const afterRemount = reads.destination;
        title = "Restored source authority";
        status = "available";
        await notify();
        await expect(mirror).toContainText(title);
        expect(reads.destination).toBe(afterRemount + 1);
        expect(errors).toEqual([]);
        const receipt = JSON.stringify({ type, initial, revoked: state, reads, writes, errors }, null, 2);
        await test.info().attach("closed-mirror-state.json", { body: receipt, contentType: "application/json" });
    });
}
