import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const html = readFileSync(fileURLToPath(new URL("../../../../api/langboard/mcp_integration/work_plan.html", import.meta.url)), "utf8");
const malicious = "<img src='https://invalid.example/attack' onerror='window.injected=true'>";
const cards = [
    {
        client_ref: "new:followup-one",
        title: `Follow-up one ${malicious}`,
        description: `Reviewed description ${malicious}`,
        assign_user_uids: ["member-one", `member-two ${malicious}`],
        deadline_at: "2026-10-23T23:59:00+09:00",
        presentation: { version: 1, key: "app.test.followup", axis: "type", name: malicious, description: "Reviewed display metadata" },
    },
    {
        client_ref: "new:followup-two",
        title: "Follow-up two",
        description: "Second reviewed description",
        assign_user_uids: ["member-three"],
        deadline_at: "2026-10-23T14:59:00Z",
    },
];

const result = (newCards: object[] = cards) => ({
    structuredContent: {
        revision: "reviewed-revision-123",
        plan: {
            anchor_card_uid: "anchor-uid",
            project_uid: "project-uid",
            new_cards: newCards,
            add_edges: cards.map((card) => ({ parent_ref: "anchor-uid", child_ref: card.client_ref, relationship_type_uid: "contains-uid" })),
            new_checklists: [{ target_card_ref: cards[0].client_ref, title: "Follow-up tasks", items: ["Reviewed task"] }],
        },
    },
});

async function send(page: Page, message: object) {
    await page.evaluate((data) => document.querySelector("iframe")!.contentWindow!.postMessage({ jsonrpc: "2.0", ...data }, "*"), message);
}

async function mount(page: Page, initialize = true) {
    await page.setContent("<main></main>");
    await page.evaluate((source) => {
        const messages: object[] = [];
        Object.assign(window, { bridgeMessages: messages });
        window.addEventListener("message", (event) => messages.push(event.data));
        const iframe = document.createElement("iframe");
        iframe.srcdoc = source;
        document.querySelector("main")!.append(iframe);
    }, html);
    await expect.poll(() => messages(page)).toContainEqual(expect.objectContaining({ method: "ui/initialize", id: "langboard-init" }));
    if (initialize) {
        await send(page, { id: "langboard-init", result: { hostContext: { theme: "dark" } } });
        await expect.poll(() => messages(page)).toContainEqual(expect.objectContaining({ method: "ui/notifications/initialized" }));
    }
    return page.frameLocator("iframe");
}

async function messages(page: Page): Promise<Record<string, unknown>[]> {
    return page.evaluate(() => Reflect.get(window, "bridgeMessages"));
}

test("shows exact reviewed creation fields, safe text and existing graph through the native bridge", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    const widget = await mount(page);
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    const articles = widget.locator("article");
    await expect(articles).toHaveCount(2);
    for (const [index, card] of cards.entries()) {
        const article = articles.nth(index);
        await expect(article.locator("strong")).toHaveText(card.title);
        await expect(article.getByText(`Assignee UIDs: ${card.assign_user_uids.join(", ")}`, { exact: true })).toBeVisible();
        await expect(article.getByText(`Deadline: ${card.deadline_at}`, { exact: true })).toBeVisible();
        await expect(article.getByText(`Reference: ${card.client_ref}`, { exact: true })).toBeVisible();
        await article.getByText("Description", { exact: true }).click();
        await expect(article.getByText(card.description, { exact: true })).toBeVisible();
    }
    await articles.first().getByText("Presentation metadata", { exact: true }).click();
    await expect(articles.first().locator("pre").last()).toHaveText(JSON.stringify(cards[0].presentation, null, 2));
    await expect(widget.locator(".edge")).toHaveCount(2);
    await expect(widget.locator(".edge").first()).toHaveText(`Anchor card→${cards[0].title}`);
    await widget.getByText("Follow-up tasks · 1 items", { exact: true }).click();
    await expect(widget.getByText("Reviewed task", { exact: true })).toBeVisible();
    await widget.getByText("Reviewed version", { exact: true }).click();
    await expect(widget.getByText("reviewed-revision-123", { exact: true })).toBeVisible();
    await expect(widget.locator("img, script[src]")).toHaveCount(0);
    expect(requests).toEqual([]);
    expect(
        (await messages(page)).every((message) =>
            ["ui/initialize", "ui/notifications/initialized", "ui/notifications/size-changed"].includes(String(message.method))
        )
    ).toBe(true);
});

test("distinguishes omitted creation fields from an explicit empty assignee list", async ({ page }) => {
    const widget = await mount(page);
    await send(page, {
        method: "ui/notifications/tool-result",
        params: result([
            { client_ref: "new:omitted", title: "Omitted fields" },
            { client_ref: "new:empty", title: "Explicit empty", description: "", assign_user_uids: [] },
        ]),
    });
    const articles = widget.locator("article");
    await expect(articles.nth(0).getByText("Assignee UIDs: Not specified", { exact: true })).toBeVisible();
    await expect(articles.nth(1).getByText("Assignee UIDs: None (explicit empty list)", { exact: true })).toBeVisible();
    await expect(articles.nth(0).getByText("Deadline: Not specified", { exact: true })).toBeVisible();
    await expect(articles.nth(1).getByText("Deadline: Not specified", { exact: true })).toBeVisible();
    await expect(articles.nth(0).getByText("Description", { exact: true })).toHaveCount(0);
    await expect(articles.nth(1).getByText("Description", { exact: true })).toHaveCount(1);
});

test("preserves parent-only initialization, host theme, cancellation and teardown", async ({ page }) => {
    const widget = await mount(page, false);
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await expect(widget.locator("#status")).toHaveText("Waiting for a reviewed plan…");
    const frame = page.frames().find((candidate) => candidate.parentFrame());
    await frame!.evaluate(() => window.postMessage({ jsonrpc: "2.0", id: "langboard-init", result: { hostContext: { theme: "dark" } } }, "*"));
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await expect(widget.locator("#view")).toBeEmpty();
    await send(page, { id: "langboard-init", result: { hostContext: { theme: "dark" } } });
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await expect(widget.locator("article")).toHaveCount(2);
    await expect(widget.locator("html")).toHaveCSS("color-scheme", "dark");
    await send(page, { method: "ui/notifications/host-context-changed", params: { theme: "light" } });
    await expect(widget.locator("html")).toHaveCSS("color-scheme", "light");
    await send(page, { method: "ui/notifications/tool-cancelled" });
    await expect(widget.locator("#view")).toBeEmpty();
    await expect(widget.locator("#status")).toHaveText("Preview cancelled.");
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await expect(widget.locator("article")).toHaveCount(2);
    await send(page, { id: "teardown-123", method: "ui/resource-teardown" });
    await expect(widget.locator("#view")).toBeEmpty();
    await expect.poll(() => messages(page)).toContainEqual({ jsonrpc: "2.0", id: "teardown-123", result: {} });
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await expect(widget.locator("#view")).toBeEmpty();
});

const locales = [
    ["en-US", "en", "Work plan preview", "Langboard work plan", "Description", "Reference", "Deadline", "Assignee UIDs", "Reviewed version"],
    ["ko-KR", "ko", "작업 계획 미리보기", "Langboard 작업 계획", "설명", "참조", "마감일", "담당자 UID", "검토된 버전"],
    ["ja-JP", "ja", "作業計画のプレビュー", "Langboard 作業計画", "説明", "参照", "期限", "担当者 UID", "レビュー済みバージョン"],
    ["zh-Hans-CN", "zh", "工作计划预览", "Langboard 工作计划", "描述", "引用", "截止时间", "负责人 UID", "已审核版本"],
];
for (const [locale, lang, heading, title, description, reference, deadline, assignees, reviewed] of locales) {
    test(`localizes actual HTML for ${locale} with exact reviewed data`, async ({ page }) => {
        const requests: string[] = [];
        page.on("request", (request) => requests.push(request.url()));
        const widget = await mount(page, false);
        await send(page, { id: "langboard-init", result: { hostContext: { locale } } });
        await expect(widget.locator("html")).toHaveAttribute("lang", lang);
        await expect(widget.locator("title")).toHaveJSProperty("textContent", title);
        await expect(widget.locator("#heading")).toHaveText(heading);
        const reviewedResult = result();
        const fullResult = {
            structuredContent: {
                ...reviewedResult.structuredContent,
                plan: {
                    ...reviewedResult.structuredContent.plan,
                    cardify_checkitems: [
                        {
                            title: "Promoted English title",
                            client_ref: "new:promoted",
                            source_card_uid: "source-uid",
                            checkitem_uid: "checkitem-uid",
                            project_column_uid: "column-uid",
                        },
                    ],
                    remove_relationship_uids: ["removed-uid"],
                },
            },
        };
        await send(page, { method: "ui/notifications/tool-result", params: fullResult });
        const frame = page.frames().find((candidate) => candidate.parentFrame())!;
        const dictionaries: Record<string, Record<string, string>> = await frame.evaluate("translations");
        const keys = Object.keys(dictionaries.ko).sort();
        for (const dictionary of Object.values(dictionaries)) {
            expect(Object.keys(dictionary).sort()).toEqual(keys);
            expect(Object.values(dictionary).every((text) => text.length > 0)).toBe(true);
        }
        const translated = (key: string) => dictionaries[lang]?.[key] ?? key;
        await expect(widget.locator("#status")).toHaveText(translated("Reviewed proposal · no changes saved"));
        await expect(widget.locator("#notice")).toHaveText(
            translated("Preview only. No changes saved. Applying requires a separate authorized tool call.")
        );
        for (const key of ["New cards", "Connections", "Promote checklist items to cards", "New checklists", "Remove connections"]) {
            await expect(widget.locator("h2").getByText(translated(key), { exact: true })).toBeVisible();
        }
        await expect(widget.locator(".summary")).toHaveText(
            ["New cards", "Promoted items", "Connections", "Removed connections", "Checklists"]
                .map((key, index) => `${translated(key)} · ${[2, 1, 2, 1, 1][index]}`)
                .join("")
        );
        await widget.getByText("Promoted English title", { exact: true }).click();
        const promotedFields = [
            `${translated("Source card")}: source-uid`,
            `${translated("Checklist item")}: checkitem-uid`,
            `${translated("Target column")}: column-uid`,
            `${translated("Reference")}: new:promoted`,
        ].join("\n");
        await expect(widget.getByText(promotedFields, { exact: true })).toBeVisible();
        await widget.getByText(translated("Relationship IDs"), { exact: true }).click();
        await expect(widget.getByText("removed-uid", { exact: true })).toBeVisible();
        await widget.getByText(`Follow-up tasks · 1 ${translated("items")}`, { exact: true }).click();
        await expect(widget.getByText("Reviewed task", { exact: true })).toBeVisible();
        await widget.locator("article").first().getByText(translated("Presentation metadata"), { exact: true }).click();
        await expect(widget.locator("article").first().locator("pre").last()).toHaveText(JSON.stringify(cards[0].presentation, null, 2));
        for (const [index, card] of cards.entries()) {
            const article = widget.locator("article").nth(index);
            await expect(article.locator("strong")).toHaveText(card.title);
            await expect(article.getByText(`${reference}: ${card.client_ref}`, { exact: true })).toBeVisible();
            await expect(article.getByText(`${deadline}: ${card.deadline_at}`, { exact: true })).toBeVisible();
            await expect(article.getByText(`${assignees}: ${card.assign_user_uids.join(", ")}`, { exact: true })).toBeVisible();
            await article.getByText(description, { exact: true }).click();
            await expect(article.getByText(card.description, { exact: true })).toBeVisible();
        }
        await widget.getByText(reviewed, { exact: true }).click();
        await expect(widget.getByText("reviewed-revision-123", { exact: true })).toBeVisible();
        expect(requests).toEqual([]);
        expect(
            (await messages(page)).every((message) =>
                ["ui/initialize", "ui/notifications/initialized", "ui/notifications/size-changed"].includes(String(message.method))
            )
        ).toBe(true);
    });
}

test("preserves details, theme, cancelled/error/empty state across host locale changes and teardown", async ({ page }) => {
    const widget = await mount(page);
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await widget.locator("article").first().getByText("Description", { exact: true }).click();
    for (const [locale, lang, heading] of locales.slice(1)) {
        await send(page, { method: "ui/notifications/host-context-changed", params: { locale } });
        await expect(widget.locator("html")).toHaveAttribute("lang", lang);
        await expect(widget.locator("#heading")).toHaveText(heading);
        await expect(widget.locator("article").first().getByText(cards[0].description, { exact: true })).toBeVisible();
        await expect(widget.locator("html")).toHaveCSS("color-scheme", "dark");
    }
    await send(page, { method: "ui/notifications/host-context-changed", params: { theme: "light" } });
    await expect(widget.locator("html")).toHaveAttribute("lang", "zh");
    await expect(widget.locator("html")).toHaveCSS("color-scheme", "light");
    for (const locale of ["fr-FR", "ko_invalid", {}, [], 42, null, ""]) {
        await send(page, { method: "ui/notifications/host-context-changed", params: { locale } });
        await expect(widget.locator("html")).toHaveAttribute("lang", "en");
    }
    await send(page, { method: "ui/notifications/tool-cancelled" });
    await send(page, { method: "ui/notifications/host-context-changed", params: { locale: "ko" } });
    await expect(widget.locator("#status")).toHaveText("미리보기가 취소되었습니다.");
    await expect(widget.locator("#view")).toBeEmpty();
    await send(page, { method: "ui/notifications/tool-result", params: { isError: true } });
    await send(page, { method: "ui/notifications/host-context-changed", params: { locale: "ja" } });
    await expect(widget.locator("#status")).toHaveText("プレビューを利用できません。再試行前に現在の状態を確認してください。");
    await expect(widget.locator("#status")).toHaveClass("error");
    await expect(widget.locator("#view")).toBeEmpty();
    await send(page, { method: "ui/notifications/tool-result", params: {} });
    await send(page, { method: "ui/notifications/host-context-changed", params: { locale: "zh" } });
    await expect(widget.locator("#status")).toHaveText("此结果中没有已审核的计划。");
    await expect(widget.locator("#view")).toBeEmpty();
    await send(page, { id: "locale-teardown", method: "ui/resource-teardown" });
    await send(page, { id: "langboard-init", result: { hostContext: { locale: "ko" } } });
    await send(page, { method: "ui/notifications/host-context-changed", params: { locale: "ko" } });
    await send(page, { method: "ui/notifications/tool-result", params: result() });
    await expect(widget.locator("html")).toHaveAttribute("lang", "zh");
    await expect(widget.locator("#view")).toBeEmpty();
});

test("uses browser locale when host locale is absent", async ({ browser }) => {
    const context = await browser.newContext({ locale: "ko-KR" });
    try {
        const page = await context.newPage();
        const widget = await mount(page, false);
        await expect(widget.locator("html")).toHaveAttribute("lang", "ko");
        await send(page, { id: "langboard-init", result: { hostContext: { theme: "dark" } } });
        await expect(widget.locator("html")).toHaveAttribute("lang", "ko");
        await send(page, { method: "ui/notifications/host-context-changed", params: { locale: "fr-FR" } });
        await expect(widget.locator("html")).toHaveAttribute("lang", "en");
    } finally {
        await context.close();
    }
});
