import { writeFile } from "node:fs/promises";
import { expect, test, type Locator, type Page } from "@playwright/test";

const evidenceDirectory = "../../.verification/workbench-narrow-status";
const phase = process.env.STATUS_EVIDENCE_PHASE || "green";

async function openStatusFixture(page: Page, language: "en-US" | "ko-KR") {
    // HTTP and socket peers are fixtures; the board, models, auth store and footer are production components.
    await page.routeWebSocket(/.*/, () => {});
    const now = new Date().toISOString();
    const user = { uid: "fixture-user", created_at: now, updated_at: now, firstname: "Fixture", lastname: "User", user_groups: [] };
    const title =
        language === "ko-KR"
            ? "좁은 화면에서도 키보드로 전체 프로젝트 이름을 확인하는 작업 상태 검증 프로젝트"
            : "Workbench status project with a deliberately long name that must remain fully readable by keyboard";
    const project = {
        uid: "left",
        title,
        created_at: now,
        updated_at: now,
        owner_uid: user.uid,
        project_type: "default",
        archive_visible_days: 30,
        all_members: [user],
        invited_member_uids: [],
        internal_bots: [],
        internal_bot_settings: {},
        current_auth_role_actions: ["*"],
        labels: [],
        description: "",
    };
    await page.route(
        (url) => url.port === "5381",
        async (route) => {
            const path = new URL(route.request().url()).pathname;
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            let json: unknown = {};
            if (path.startsWith("/auth/")) json = { access_token: "fixture", user, bots: [] };
            else if (path === "/board/left") json = { project };
            else if (path === "/board/left/cards") json = { cards: [], columns: [], checklists: [], global_relationships: [] };
            else if (path.includes("/column/dock")) json = { column_uids: [] };
            else if (path.includes("/cards/available")) json = { card_uids: [] };
            else if (path.includes("project")) json = { projects: [project], columns: [], records: [], collections: [] };
            else if (path.includes("apps")) json = { apps: [] };
            await route.fulfill({ json });
        }
    );
    await page.goto("/src/pages/BoardPage/BoardSplit.fixture.html");
    const footer = page.locator("[data-workbench-status]");
    await expect(footer).toContainText(title, { timeout: 45_000 });
    await page.evaluate(async (locale) => {
        const path = "/src/i18n.ts";
        const { default: i18n } = await import(/* @vite-ignore */ path);
        await i18n.changeLanguage(locale);
    }, language);
    await expect(footer).toHaveAccessibleName(language === "ko-KR" ? "작업 상태" : "Workbench status");
    return { footer, title };
}

async function tabIntoFooter(page: Page, footer: Locator) {
    // Start immediately before the footer in the real DOM; one native Tab decides reachability.
    const previous = await footer.evaluate((element) => {
        const candidates = Array.from(document.querySelectorAll<HTMLElement>("a[href],button,input,select,textarea,[tabindex]"));
        const previous = candidates
            .filter(
                (candidate) =>
                    candidate.tabIndex >= 0 &&
                    !candidate.matches(":disabled") &&
                    !candidate.closest("[inert]") &&
                    candidate.getClientRects().length &&
                    getComputedStyle(candidate).visibility !== "hidden" &&
                    !!(candidate.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
            )
            .at(-1);
        if (!previous) throw new Error("Fixture has no preceding keyboard stop");
        previous.focus();
        return { tag: previous.tagName, label: previous.getAttribute("aria-label"), text: previous.textContent };
    });
    await page.keyboard.press("Tab");
    return previous;
}

for (const language of ["en-US", "ko-KR"] as const) {
    test(`all footer fields remain readable and keyboard reachable in ${language}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width: 320, height: 844 });
        const { footer, title } = await openStatusFixture(page, language);
        const scroll = footer.locator(".overflow-x-auto");
        const expected =
            language === "ko-KR"
                ? [title, "보드", "미열람: 0", "멤버: 1", "권한: 조회, 수정, 카드 생성, 카드 수정, 카드 삭제", "MCP: 미확인"]
                : [title, "Board", "Unread: 0", "Members: 1", "Permissions: Read, Update, Card Write, Card Update, Card Delete", "MCP: Unverified"];
        const receipts = [];
        for (const width of [320, 390, 768, 1440]) {
            await page.setViewportSize({ width, height: 844 });
            await scroll.evaluate((element) => {
                element.scrollLeft = 0;
            });
            const previous = await tabIntoFooter(page, footer);
            const focused = await scroll.evaluate((element) => document.activeElement === element);
            const geometry = await scroll.evaluate((element) => ({
                width: element.clientWidth,
                scrollWidth: element.scrollWidth,
                focusVisible: element.matches(":focus-visible"),
                outline: getComputedStyle(element).outlineStyle,
                fields: Array.from(element.children).map((field) => ({
                    text: field.textContent,
                    width: field.clientWidth,
                    scrollWidth: field.scrollWidth,
                    overflow: getComputedStyle(field).overflowX,
                    textOverflow: getComputedStyle(field).textOverflow,
                    start: field.getBoundingClientRect().left - element.getBoundingClientRect().left,
                    end: field.getBoundingClientRect().right - element.getBoundingClientRect().left,
                })),
            }));
            const accessibility = await footer.ariaSnapshot();
            const before = await scroll.evaluate((element) => element.scrollLeft);
            await footer.screenshot({ path: `${evidenceDirectory}/${phase}-${language}-${width}-start.png` });
            if (focused && geometry.scrollWidth > geometry.width) {
                await page.keyboard.press("ArrowRight");
                await expect.poll(() => scroll.evaluate((element) => element.scrollLeft)).toBeGreaterThan(before);
                // Native arrow scrolling has a bounded endpoint; no exhaustive page Tab traversal.
                await expect
                    .poll(
                        async () => {
                            await page.keyboard.press("ArrowRight");
                            return scroll.evaluate((element) => element.scrollWidth - element.clientWidth - element.scrollLeft);
                        },
                        { timeout: 8000, intervals: [50] }
                    )
                    .toBeLessThanOrEqual(1);
            }
            const after = await scroll.evaluate((element) => element.scrollLeft);
            const bounds = (await footer.boundingBox())!;
            const realtime = (await footer.locator("[data-status-realtime]").boundingBox())!;
            const navigation = (await page.locator("nav[data-workbench-context]").boundingBox())!;
            await footer.screenshot({ path: `${evidenceDirectory}/${phase}-${language}-${width}-end.png` });
            if (focused && after > 0) {
                await page.keyboard.press("ArrowLeft");
                await expect.poll(() => scroll.evaluate((element) => element.scrollLeft)).toBeLessThan(after);
                await page.keyboard.press("Tab");
                await expect(scroll).not.toBeFocused();
            }
            receipts.push({ width, language, previous, focused, before, after, geometry, accessibility, bounds, realtime, navigation });
            await writeFile(`${evidenceDirectory}/${phase}-${language}.json`, JSON.stringify(receipts, null, 2));
            for (const text of expected) expect.soft(accessibility).toContain(text);
            expect.soft(geometry.fields.map((field) => field.text)).toEqual(expected);
            for (const field of geometry.fields) {
                expect.soft(field.scrollWidth, `${language} ${width}px: ${field.text}`).toBeLessThanOrEqual(field.width);
                expect.soft(field.start).toBeGreaterThanOrEqual(0);
                expect.soft(field.end).toBeLessThanOrEqual(geometry.scrollWidth + 1);
            }
            if (geometry.scrollWidth > geometry.width) {
                expect.soft(focused, `${width}px native Tab enters the scrollable footer`).toBe(true);
                expect.soft(geometry.focusVisible).toBe(true);
                expect.soft(geometry.outline).not.toBe("none");
            }
            expect.soft(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
            expect.soft(bounds.width).toBe(width);
            expect.soft(realtime.x).toBeGreaterThanOrEqual(0);
            expect.soft(realtime.x + realtime.width).toBeLessThanOrEqual(width);
            expect.soft(bounds.y + bounds.height).toBeLessThanOrEqual(844);
            if (width < 1024) expect.soft(bounds.y + bounds.height).toBeLessThanOrEqual(navigation.y);
            else expect.soft(bounds.y + bounds.height).toBe(844);
        }
        await testInfo.attach(`${language}-focus-overflow-accessibility`, {
            body: JSON.stringify(receipts, null, 2),
            contentType: "application/json",
        });
        await page.evaluate(async () => {
            const path = "/src/core/models/index.ts";
            const { Project } = await import(/* @vite-ignore */ path);
            Project.Model.getModel("left").title = "Short project";
        });
        await expect(footer.locator("[data-status-project]")).toHaveText("Short project");
        expect(await scroll.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    });
}
