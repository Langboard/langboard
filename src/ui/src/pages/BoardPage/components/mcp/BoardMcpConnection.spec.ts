import { expect, test, Page } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/mcp/BoardMcpConnection.fixture.html";
const url = "https://langboard.example/mcp";
async function transport(page: Page, enabled = true) {
    const writes: { path: string; body: unknown }[] = [];
    let scopes: string[] = [];
    await page.route("**/mcp/connections**", (route) => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith("/connections"))
            return route.fulfill({
                json: {
                    server: { name: "langboard", transport_url: url, resource: url, enabled },
                    connections: enabled ? [{ uid: "ConnectionA", client_id: "authenticated-client" }] : [],
                },
            });
        if (route.request().method() === "PUT") {
            const body = route.request().postDataJSON();
            writes.push({ path, body });
            scopes = body.scopes;
        }
        return route.fulfill({
            json: {
                connection_uid: "ConnectionA",
                project_uid: path.split("/").at(-1),
                revision: scopes.length ? 1 : null,
                scopes,
                available_scopes: ["read", "write"],
            },
        });
    });
    await page.addInitScript(() => {
        Object.defineProperty(navigator, "clipboard", {
            value: {
                writeText: async (value: string) => {
                    (window as unknown as { copied: string }).copied = value;
                },
            },
        });
    });
    await page.goto(fixture);
    return writes;
}

test("production chooser: keyboard search, details, Advanced and fixed configuration across boards", async ({ page }) => {
    const writes = await transport(page);
    const search = page.getByRole("textbox", { name: "Search clients" });
    await search.fill("codex");
    await expect(page.getByRole("button", { name: /Claude Code/ })).toHaveCount(0);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Codex", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "All clients" })).toBeFocused();
    await expect(page.locator("pre").first()).toContainText("codex mcp login 'langboard'");
    await page.getByRole("button", { name: "Copy config", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Copied." })).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { copied: string }).copied)).toContain(url);
    expect(writes).toHaveLength(0);
    await page.getByRole("button", { name: "Switch board" }).click();
    await expect(page.getByText("BoardB", { exact: true })).toBeVisible();
    await expect(page.locator("pre").first()).toContainText(url);
    await page.getByRole("button", { name: "All clients" }).click();
    await expect(search).toBeFocused();
    await search.fill("VS Code");
    await page.getByRole("button", { name: /VS Code Open/ }).click();
    const link = await page.getByRole("link", { name: "One-click install" }).getAttribute("href");
    expect(JSON.parse(decodeURIComponent(link!.split("?")[1]))).toEqual({ name: "langboard", type: "http", url });
    await page.getByText("Advanced", { exact: true }).click();
    await expect(page.locator("details pre").first()).toContainText("mcpServers");
    await expect(page.locator("details")).toContainText(url);
    await expect(page.locator("html")).toHaveJSProperty("scrollWidth", 320);
});

for (const denied of [false, true])
    test(`copy status follows the current locale without another clipboard write; denied=${denied}`, async ({ page }) => {
        const writes = await transport(page);
        let copies = 0;
        await page.exposeFunction("recordCopy", () => copies++);
        await page.evaluate((denied) => {
            navigator.clipboard.writeText = async () => {
                await (window as unknown as { recordCopy: () => Promise<void> }).recordCopy();
                if (denied) throw new Error("denied");
            };
        }, denied);
        await page.getByRole("textbox", { name: "Search clients" }).fill("Codex");
        await page
            .getByRole("button")
            .filter({ has: page.getByText("Codex", { exact: true }) })
            .click();
        await page.getByRole("button", { name: "Copy config", exact: true }).click();
        await expect.poll(() => copies).toBe(1);
        for (const locale of ["ko-KR", "ja-JP", "zh-CN", "en-US"]) {
            const expected = await page.evaluate(
                async ({ locale, denied }) => {
                    const modulePath = "/src/i18n.ts";
                    const i18n = (await import(/* @vite-ignore */ modulePath)).default;
                    await i18n.changeLanguage(locale);
                    return i18n.t(denied ? "mcp.Copy failed" : "mcp.Copied configuration");
                },
                { locale, denied }
            );
            await expect(page.getByRole("status").filter({ hasText: expected })).toBeVisible();
            expect(copies).toBe(1);
            expect(writes).toHaveLength(0);
        }
    });

test("client switching preserves board consent; copies never approve; consent save and revoke use current board", async ({ page }) => {
    const writes = await transport(page);
    for (const client of ["Codex", "Claude Code", "Claude", "Cursor", "VS Code", "Hermes", "OpenClaw"]) {
        await page.getByRole("textbox", { name: "Search clients" }).fill(client);
        await page
            .getByRole("button")
            .filter({ has: page.getByText(client, { exact: true }) })
            .click();
        await expect(page.getByRole("heading", { name: client, exact: true })).toBeVisible();
        if (client === "Cursor") {
            const href = await page.getByRole("link", { name: "One-click install" }).getAttribute("href");
            const params = new URL(href!).searchParams;
            expect(params.get("name")).toBe("langboard");
            expect(JSON.parse(Buffer.from(params.get("config")!, "base64").toString())).toEqual({ url });
        }
        await page.getByRole("button", { name: "Copy config", exact: true }).click();
        await page.getByRole("button", { name: "All clients" }).click();
    }
    expect(writes).toHaveLength(0);
    await page.getByLabel("Choose your verified client connection").selectOption("ConnectionA");
    await page.getByRole("checkbox", { name: "Read this board" }).check();
    await page.getByRole("button", { name: "Save board access" }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toEqual({ path: "/mcp/connections/ConnectionA/boards/BoardA", body: { scopes: ["read"], expected_revision: null } });
    await page.getByRole("button", { name: "Revoke this board" }).click();
    await expect.poll(() => writes.length).toBe(2);
    expect(writes[1].body).toEqual({ scopes: [], expected_revision: 1 });
    await page.getByRole("button", { name: "Switch board" }).click();
    await page.getByRole("checkbox", { name: "Read this board" }).check();
    await page.getByRole("button", { name: "Save board access" }).click();
    await expect.poll(() => writes.length).toBe(3);
    expect(writes[2].path).toContain("/boards/BoardB");
});

test("disabled configuration hides installation and cannot fabricate an authenticated connection", async ({ page }) => {
    const writes = await transport(page, false);
    await expect(page.getByText("Native MCP is not available yet. Existing connections can be reviewed below.")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Search clients" })).toHaveCount(0);
    await expect(page.getByText(url, { exact: true })).toHaveCount(0);
    await expect(page.getByRole("option")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Save board access" })).toHaveCount(0);
    expect(writes).toHaveLength(0);
});

test("copy failure permits retry and does not claim connection success; empty search recovers", async ({ page }) => {
    await transport(page);
    await page.getByRole("textbox", { name: "Search clients" }).fill("missing");
    await expect(page.getByRole("status").filter({ hasText: "No matching clients" })).toBeVisible();
    await page.getByRole("textbox", { name: "Search clients" }).fill("Claude Code");
    await page.getByRole("button", { name: /Claude Code Copy/ }).click();
    await page.evaluate(() => {
        navigator.clipboard.writeText = async () => {
            throw new Error("denied");
        };
    });
    await page.getByRole("button", { name: "Copy config", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Copy failed" })).toBeVisible();
    await page.evaluate(() => {
        navigator.clipboard.writeText = async () => {};
    });
    await page.getByRole("button", { name: "Copy config", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Copied." })).toBeVisible();
});
