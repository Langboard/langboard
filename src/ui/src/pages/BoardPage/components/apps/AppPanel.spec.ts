import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
const sdk = readFileSync("node_modules/@langboard/app-panel/index.mjs", "utf8");
test("independent resource panel pins authority, rejects identity injection and cancels on close", async ({ page }) => {
    const writes: unknown[] = [];
    let release: () => void = () => {};
    let pending = false;
    let denied = false;
    const delayed = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/panel-sdk-resources.json", (r) => r.fulfill({ json: { version: 1, module_url: "/widgets.mjs", css_url: "/widgets.css" } }));
    await page.route("**/board/board/apps/example-app/panel", (r) =>
        r.fulfill({
            json: {
                key: "example-app",
                version: "1.0.0",
                panel: { url: "https://panel.example.invalid/panel", name: "Independent resources" },
                app_revision: "a".repeat(64),
                binding_revision: "b".repeat(64),
            },
        })
    );
    await page.route("https://panel.example.invalid/sdk.mjs", (r) =>
        r.fulfill({ body: sdk, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" } })
    );
    await page.route("https://panel.example.invalid/panel", (r) =>
        r.fulfill({
            contentType: "text/html",
            body: `
        <button id="read">Read</button><button id="inject">Inject identity</button><pre id="result">Connecting</pre>
        <script type="module">
        import {connectPanel} from './sdk.mjs';
        const output = document.querySelector('#result');
        const session = await connectPanel({expectedHostOrigin: 'http://127.0.0.1:4181',
            onShow: () => {output.textContent = 'Ready';}, onDispose: () => {}});
        async function read(params) {
            try {output.textContent = JSON.stringify(await session.request('resources.list', params));}
            catch (error) {output.textContent = 'Denied: ' + error.message;}
        }
        document.querySelector('#read').onclick = () => read({});
        document.querySelector('#inject').onclick = () => read({app_key:'other-app'});
        </script>`,
        })
    );
    await page.route("**/board/board/apps/example-app/panel/resources", async (r) => {
        writes.push(r.request().postDataJSON());
        if (pending) await delayed;
        await r.fulfill({ status: denied ? 404 : 200, json: { items: [{ external_resource_id: "issue-1" }], next_cursor: null } }).catch(() => {});
    });
    await page.goto("/src/pages/BoardPage/components/apps/AppPanel.fixture.html");
    const panel = page.frameLocator("iframe");
    await expect(panel.locator("#result")).toHaveText("Ready");
    await panel.getByRole("button", { name: "Read", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("issue-1", { timeout: 7000 });
    expect(writes).toEqual([{ app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
    await panel.getByRole("button", { name: "Inject identity" }).click();
    await expect(panel.locator("#result")).toContainText("Denied");
    expect(writes).toHaveLength(1);
    denied = true;
    await panel.getByRole("button", { name: "Read", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("Denied");
    expect(writes).toHaveLength(2);
    denied = false;
    pending = true;
    const next = page.waitForRequest("**/panel/resources");
    await panel.getByRole("button", { name: "Read", exact: true }).click();
    const active = await next;
    const aborted = page.waitForEvent("requestfailed", { predicate: (r) => r === active });
    await page.getByRole("button", { name: "Close App panel" }).click();
    await expect(page.getByText("Panel closed", { exact: true })).toBeVisible();
    expect((await aborted).failure()?.errorText).toContain("ERR_ABORTED");
    release();
    await expect(page.locator("iframe")).toHaveCount(0);
    expect(writes).toHaveLength(3);
});
