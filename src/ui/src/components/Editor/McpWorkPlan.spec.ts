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
