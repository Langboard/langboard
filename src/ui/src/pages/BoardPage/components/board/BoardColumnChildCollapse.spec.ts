import { expect, test } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/board/BoardColumnChildCollapse.fixture.html";
const row = (page: import("@playwright/test").Page, uid: string) => page.locator(`[data-board-card-touch-dnd-uid="${uid}"]`);
const control = (page: import("@playwright/test").Page, uid: string) => row(page, uid).getByRole("button", { name: /child cards/ });
const saved = (page: import("@playwright/test").Page) =>
    page.evaluate(() => JSON.parse(localStorage.getItem("user-settings-store") ?? "{}").child_card_collapsed ?? {});

test("native nested collapse persists, isolates scopes, reveals only target ancestors and shrinks measured height", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/board/**/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
    await page.goto(fixture);
    await expect(control(page, "root")).toHaveText("4");
    await expect(control(page, "child")).toHaveText("1");
    const group = page.locator("[data-index]").first();
    const initialHeight = await group.evaluate((node) => node.getBoundingClientRect().height);
    const initialScrollHeight = await page.getByTestId("scrollport").evaluate((node) => node.scrollHeight);
    const parentHeight = await page.locator("#board-card-root").evaluate((node) => node.getBoundingClientRect().height);
    await control(page, "sibling").tap();
    await expect(row(page, "cousin")).toHaveCount(0);
    await control(page, "child").focus();
    await page.keyboard.press("Space");
    await expect(row(page, "grandchild")).toHaveCount(0);
    await control(page, "root").click();
    await expect(row(page, "child")).toHaveCount(0);
    await expect(control(page, "root")).toHaveAttribute("aria-expanded", "false");
    await expect.poll(async () => group.evaluate((node) => node.getBoundingClientRect().height)).toBeLessThan(initialHeight - 50);
    await expect.poll(() => page.getByTestId("scrollport").evaluate((node) => node.scrollHeight)).toBeLessThan(initialScrollHeight);
    expect(await page.locator("#board-card-root").evaluate((node) => node.getBoundingClientRect().height)).toBe(parentHeight);
    await expect.poll(() => saved(page)).toEqual({ "user:board:child": true, "user:board:root": true, "user:board:sibling": true });
    await page.reload();
    await expect(row(page, "grandchild")).toHaveCount(0);
    await control(page, "root").click();
    await expect(row(page, "child")).toHaveCount(1);
    await expect(row(page, "grandchild")).toHaveCount(0);
    await control(page, "root").click();
    await expect.poll(() => saved(page)).toEqual({ "user:board:child": true, "user:board:root": true, "user:board:sibling": true });
    const before = await saved(page);
    await page.getByRole("button", { name: "Measure grandchild" }).click();
    await expect(row(page, "grandchild")).toHaveCount(0);
    await page.getByRole("button", { name: "Focus grandchild" }).click();
    await expect(row(page, "grandchild")).toHaveCount(1);
    await expect(row(page, "grandchild")).toHaveAttribute("data-relationship-drop-target", "true");
    await expect(row(page, "cousin")).toHaveCount(0);
    expect(await saved(page)).toEqual(before);
    await page.reload();
    await expect(row(page, "grandchild")).toHaveCount(0);
    await page.getByRole("button", { name: "Locate grandchild" }).click();
    await expect(row(page, "grandchild")).toHaveCount(1);
    await expect(row(page, "cousin")).toHaveCount(0);
    expect(await saved(page)).toEqual(before);
    await page.goto(`${fixture}?user=other`);
    await expect(row(page, "grandchild")).toHaveCount(1);
    await page.goto(`${fixture}?board=other`);
    await expect(row(page, "grandchild")).toHaveCount(1);
    expect(await saved(page)).toEqual(before);
    expect(errors).toEqual([]);
});

test("filtered counts and translated controls preserve the filtered hierarchy", async ({ page }) => {
    await page.goto(`${fixture}?filtered&lang=ko-KR`);
    await expect(row(page, "root").getByRole("button", { name: "하위 카드 접기", exact: true })).toHaveText("2");
    await expect(row(page, "grandchild")).toHaveCount(0);
    await expect(row(page, "cousin")).toHaveCount(0);
    await expect(row(page, "child").getByRole("button", { name: "하위 카드 접기", exact: true })).toHaveCount(0);
    await row(page, "root").getByRole("button", { name: "하위 카드 접기", exact: true }).click();
    await expect(row(page, "child")).toHaveCount(0);
    await expect(row(page, "sibling")).toHaveCount(0);
    await expect(row(page, "root").getByRole("button", { name: "하위 카드 펼치기", exact: true })).toHaveText("2");
});

type TUpdateBridge = {
    order: (column: string, data: Record<string, unknown>) => Promise<void>;
    relationships: (data: Record<string, unknown>) => Promise<void>;
    snapshot: () => {
        uid: string;
        order: number;
        column: string;
        relationships: { uid: string; parent: string; child: string; semantic: string }[];
    }[];
};
const orderEvent = (page: import("@playwright/test").Page, column: string, data: Record<string, unknown>) =>
    page.evaluate(({ column, data }) => (window as unknown as { collapseUpdates: TUpdateBridge }).collapseUpdates.order(column, data), {
        column,
        data,
    });
const relationEvent = (page: import("@playwright/test").Page, data: Record<string, unknown>) =>
    page.evaluate((data) => (window as unknown as { collapseUpdates: TUpdateBridge }).collapseUpdates.relationships(data), data);
const snapshot = (page: import("@playwright/test").Page) =>
    page.evaluate(() => (window as unknown as { collapseUpdates: TUpdateBridge }).collapseUpdates.snapshot());
const edge = (parent: string, child: string, semantic = "contains") => ({
    uid: `${parent}-${child}`,
    parent_card_uid: parent,
    child_card_uid: child,
    machine_semantic: semantic,
});

test("native order converters move cards away and back while preserving nested collapse", async ({ page }) => {
    await page.goto(`${fixture}?updates`);
    await expect(control(page, "root")).toHaveText("4");
    await control(page, "child").click();
    await control(page, "root").click();
    await expect.poll(() => saved(page)).toEqual({ "user:board:child": true, "user:board:root": true });
    const initialRelationships = (await snapshot(page)).map(({ uid, relationships }) => ({ uid, relationships }));
    await orderEvent(page, "column", { uid: "unrelated", move_type: "in_column", order: 0 });
    await expect.poll(async () => (await snapshot(page)).find(({ uid }) => uid === "unrelated")?.order).toBe(0);
    await expect(page.getByTestId("scrollport").locator("[data-board-card-touch-dnd-uid]").first()).toHaveAttribute(
        "data-board-card-touch-dnd-uid",
        "unrelated"
    );
    await expect(control(page, "root")).toHaveText("4");
    await expect(row(page, "child")).toHaveCount(0);
    const move = async (uid: string, from: string, to: string) => {
        await orderEvent(page, from, { uid, move_type: "from_column", column_uid: from, order: 0 });
        await orderEvent(page, to, { uid, move_type: "to_column", column_uid: to, order: 0 });
        await expect.poll(async () => (await snapshot(page)).find((card) => card.uid === uid)?.column).toBe(to);
    };
    await move("child", "column", "other-column");
    await expect(control(page, "root")).toHaveText("2");
    await expect(page.getByTestId("other-scrollport").locator("[data-board-card-touch-dnd-uid=child]")).toHaveCount(1);
    await expect(row(page, "grandchild")).toHaveCount(1);
    await move("grandchild", "column", "other-column");
    await expect(control(page, "child")).toHaveText("1");
    await expect(control(page, "child")).toHaveAttribute("aria-expanded", "false");
    await expect(row(page, "grandchild")).toHaveCount(0);
    await move("child", "other-column", "column");
    await expect(control(page, "root")).toHaveText("3");
    await expect(row(page, "child")).toHaveCount(0);
    await expect(row(page, "grandchild")).toHaveCount(1);
    await move("grandchild", "other-column", "column");
    await expect(control(page, "root")).toHaveText("4");
    await expect(page.getByTestId("other-scrollport").locator("[data-board-card-touch-dnd-uid]")).toHaveCount(0);
    expect(await saved(page)).toEqual({ "user:board:child": true, "user:board:root": true });
    expect((await snapshot(page)).map(({ uid, relationships }) => ({ uid, relationships }))).toEqual(initialRelationships);
    await control(page, "root").click();
    await expect(control(page, "child")).toHaveAttribute("aria-expanded", "false");
    await expect(row(page, "grandchild")).toHaveCount(0);
});

test("native relationship sync updates hidden counts and semantic grouping under sort and filters", async ({ page }) => {
    await page.goto(`${fixture}?updates`);
    await expect(control(page, "root")).toHaveText("4");
    await control(page, "child").click();
    await control(page, "root").click();
    await expect.poll(() => saved(page)).toEqual({ "user:board:child": true, "user:board:root": true });
    const before = await saved(page);
    await relationEvent(page, { card_uid: "root", relationships: [edge("root", "child"), edge("root", "sibling"), edge("root", "unrelated")] });
    await expect(control(page, "root")).toHaveText("5");
    await expect(row(page, "unrelated")).toHaveCount(0);
    await relationEvent(page, { card_uid: "root", relationships: [edge("root", "child"), edge("root", "unrelated")] });
    await expect(control(page, "root")).toHaveText("3");
    await expect(row(page, "sibling")).toHaveCount(1);
    await expect(row(page, "cousin")).toHaveCount(1);
    await relationEvent(page, { card_uid: "root", relationships: [edge("root", "child", "references"), edge("root", "unrelated")] });
    await expect(control(page, "root")).toHaveText("1");
    expect((await snapshot(page)).find(({ uid }) => uid === "root")?.relationships).toEqual([
        { uid: "root-child", parent: "root", child: "child", semantic: "references" },
        { uid: "root-unrelated", parent: "root", child: "unrelated", semantic: "contains" },
    ]);
    await expect(control(page, "child")).toHaveAttribute("aria-expanded", "false");
    await expect(row(page, "grandchild")).toHaveCount(0);
    await relationEvent(page, { card_uid: "root", relationships: [edge("root", "child"), edge("root", "sibling")] });
    await expect(control(page, "root")).toHaveText("4");
    await page.getByRole("combobox", { name: "Sort cards" }).selectOption("updated_newest");
    await orderEvent(page, "column", { uid: "unrelated", move_type: "in_column", order: 5, updated_at: "2030-01-01T00:00:00Z" });
    await expect(page.getByTestId("scrollport").locator("[data-board-card-touch-dnd-uid]").first()).toHaveAttribute(
        "data-board-card-touch-dnd-uid",
        "unrelated"
    );
    await expect(control(page, "root")).toHaveText("4");
    expect(await saved(page)).toEqual(before);
    await page.goto(`${fixture}?updates&filtered`);
    await expect(control(page, "root")).toHaveText("2");
    await relationEvent(page, { card_uid: "root", relationships: [edge("root", "child"), edge("root", "sibling"), edge("root", "unrelated")] });
    await expect(control(page, "root")).toHaveText("3");
    await expect(row(page, "unrelated")).toHaveCount(0);
    expect(await saved(page)).toEqual(before);
});

for (const width of [1000, 390]) {
    for (const theme of ["light", "dark"]) {
        test(`collapse control keyboard and touch preserve card focus and body at ${width}px ${theme}`, async ({ page }) => {
            await page.setViewportSize({ width, height: 800 });
            await page.goto(fixture);
            await page.evaluate((theme) => document.documentElement.classList.toggle("dark", theme === "dark"), theme);
            const root = control(page, "root");
            await expect(root).toHaveText("4");
            const body = page.locator("#board-card-root");
            const bodyHeight = await body.evaluate((node) => node.getBoundingClientRect().height);
            const initialModels = await snapshot(page);
            // Reach the control through real Tab navigation rather than programmatic focus.
            await page.getByRole("button", { name: "Measure grandchild" }).focus();
            for (let attempt = 0; attempt < 20 && !(await root.evaluate((node) => node === document.activeElement)); attempt++) {
                await page.keyboard.press("Tab");
            }
            await expect(root).toBeFocused();
            await page.keyboard.press("Enter");
            await expect(root).toHaveAttribute("aria-expanded", "false");
            await expect(root).toBeFocused();
            await expect(row(page, "child")).toHaveCount(0);
            await page.keyboard.press("Space");
            await expect(root).toHaveAttribute("aria-expanded", "true");
            await expect(root).toBeFocused();
            await expect(row(page, "grandchild")).toHaveCount(1);
            await control(page, "child").tap();
            await root.tap();
            await expect(row(page, "child")).toHaveCount(0);
            await root.tap();
            await expect(control(page, "child")).toHaveAttribute("aria-expanded", "false");
            await expect(root).toBeFocused();
            await expect(row(page, "grandchild")).toHaveCount(0);
            await expect(page.getByTestId("location")).toHaveText("/board/board");
            expect(await body.evaluate((node) => node.getBoundingClientRect().height)).toBe(bodyHeight);
            expect(await snapshot(page)).toEqual(initialModels);
            expect(
                await page.evaluate(() => (window as unknown as { collapseInteractions: { dragStarts: number } }).collapseInteractions.dragStarts)
            ).toBe(0);
            expect(
                await root.evaluate((node) => {
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0 && style.visibility === "visible" && Number(style.opacity) > 0;
                })
            ).toBe(true);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await page.screenshot({ path: `/tmp/langboard-child-collapse-${width}-${theme}.png`, fullPage: true });
        });
    }
}

test("native physical drag remains available on the card and is suppressed on its collapse control", async ({ page }) => {
    await page.goto(fixture);
    const root = control(page, "root");
    await expect(root).toHaveText("4");
    const initialModels = await snapshot(page);
    const dragFrom = async (x: number, y: number) => {
        await page.mouse.move(x, y);
        await page.mouse.down();
        await page.mouse.move(x + 65, y + 35, { steps: 12 });
        await page.mouse.up();
    };
    // Blank title-header space is part of the actual registered draggable card.
    const body = await page.locator("#board-card-root").boundingBox();
    expect(body).not.toBeNull();
    await dragFrom(body!.x + body!.width - 25, body!.y + 20);
    await expect
        .poll(() => page.evaluate(() => (window as unknown as { collapseInteractions: { dragStarts: number } }).collapseInteractions.dragStarts))
        .toBe(1);
    const button = await root.boundingBox();
    expect(button).not.toBeNull();
    await dragFrom(button!.x + button!.width / 2, button!.y + button!.height / 2);
    expect(await page.evaluate(() => (window as unknown as { collapseInteractions: { dragStarts: number } }).collapseInteractions.dragStarts)).toBe(
        1
    );
    expect(await snapshot(page)).toEqual(initialModels);
    await expect(page.getByTestId("location")).toHaveText("/board/board");
});

for (const entry of ["deep link", "search result", "delayed hierarchy"]) {
    test(`actual ${entry} route reveals hidden ancestors without changing preferences or viewer focus`, async ({ page }) => {
        await page.addInitScript(() => {
            const highlights: string[] = [];
            Object.assign(window, { routeHighlights: highlights });
            new MutationObserver((records) => {
                for (const { target } of records) {
                    if (target instanceof HTMLElement && target.dataset.relationshipDropTarget === "true") {
                        highlights.push(target.dataset.boardCardTouchDndUid ?? "");
                    }
                }
            }).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-relationship-drop-target"] });
        });
        await page.goto(fixture);
        await control(page, "sibling").click();
        await control(page, "child").click();
        await control(page, "root").click();
        await expect.poll(() => saved(page)).toEqual({ "user:board:root": true, "user:board:child": true, "user:board:sibling": true });
        const before = await saved(page);
        await page.getByRole("link", { name: "Other project card result" }).click();
        await expect(page.getByTestId("location")).toHaveText("/board/different/grandchild");
        await expect(row(page, "grandchild")).toHaveCount(0);
        expect(await saved(page)).toEqual(before);
        if (entry === "deep link") await page.goto(`${fixture}?route`);
        else {
            if (entry === "delayed hierarchy") {
                await page.goto(`${fixture}?route&filtered`);
                await expect(control(page, "root")).toHaveText("2");
                await expect(row(page, "grandchild")).toHaveCount(0);
            }
            await page.getByRole("link", { name: "Search grandchild result" }).click();
        }
        await expect(page.getByTestId("location")).toHaveText("/board/board/grandchild");
        await expect(page.getByRole("textbox", { name: "Viewer focus sentinel" })).toBeFocused();
        await expect(row(page, "grandchild")).toHaveCount(1);
        await expect.poll(() => page.evaluate(() => (window as unknown as { routeHighlights: string[] }).routeHighlights)).toContain("grandchild");
        await expect(row(page, "cousin")).toHaveCount(0);
        expect(await saved(page)).toEqual(before);
        await expect(page.getByRole("textbox", { name: "Viewer focus sentinel" })).toBeFocused();
        // Ordinary hierarchy updates must not replay route reveal after an explicit collapse.
        await control(page, "root").click();
        await relationEvent(page, { card_uid: "root", relationships: [edge("root", "child")] });
        await expect(control(page, "root")).toHaveText("2");
        await expect(row(page, "grandchild")).toHaveCount(0);
        expect(await saved(page)).toEqual(before);
        // A route switch supersedes a pending native focus request before its frame runs.
        await page.evaluate(async () => {
            (window as unknown as { routeHighlights: string[] }).routeHighlights.length = 0;
            document.dispatchEvent(new CustomEvent("board-card-focus", { detail: { cardUID: "grandchild", columnUID: "column", focus: true } }));
            Array.from(document.links)
                .find((link) => link.pathname === "/board/different/grandchild")!
                .click();
            await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
        });
        await expect(page.getByTestId("location")).toHaveText("/board/different/grandchild");
        await expect(row(page, "grandchild")).not.toHaveAttribute("data-relationship-drop-target", "true");
        const otherProjectLink = page.getByRole("link", { name: "Other project card result" });
        await otherProjectLink.focus();
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await expect(otherProjectLink).toBeFocused();
        await expect(row(page, "grandchild")).not.toHaveAttribute("data-relationship-drop-target", "true");
    });
}
