import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
const sdk = readFileSync("node_modules/@langboard/app-panel/index.mjs", "utf8");
test("independent resource panel pins authority, rejects identity injection and cancels on close", async ({ page }) => {
    const writes: unknown[] = [];
    const signalReads: unknown[] = [];
    const interactionReads: unknown[] = [];
    let release: () => void = () => {};
    let pending = false;
    let status = 200;
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
        <button id="interactions">Interactions</button><button id="signals">Signals</button><button id="save">Save draft</button>
        <button id="read">Read</button><button id="inject">Inject identity</button><pre id="result">Connecting</pre>
        <script type="module">
        import {connectPanel} from './sdk.mjs';
        const output = document.querySelector('#result');
        const session = await connectPanel({expectedHostOrigin: 'http://127.0.0.1:4181',
            onShow: () => {}, onDispose: () => {}});
        async function read(params) {
            try {output.textContent = JSON.stringify(await session.request('resources.list', params));}
            catch (error) {output.textContent = 'Denied: ' + error.message;}
        }
        document.querySelector('#interactions').onclick = async () => {
            output.textContent = JSON.stringify(await session.request('interactions.list', {card_uid:'A1234567890',limit:10}));
        };
        document.querySelector('#signals').onclick = async () => {
            output.textContent = JSON.stringify(await session.request('signals.list', {provider:'github'}));
        };
        document.querySelector('#save').onclick = () => session.saveState({draft:'preserved'});
        document.querySelector('#read').onclick = () => read({});
        document.querySelector('#inject').onclick = () => read({app_key:'other-app'});
        output.textContent = 'Ready';
        </script>`,
        })
    );
    await page.route("**/board/board/apps/example-app/panel/resources", async (r) => {
        writes.push(r.request().postDataJSON());
        if (pending) await delayed;
        await r.fulfill({ status, json: { items: [{ external_resource_id: "issue-1" }], next_cursor: null } }).catch(() => {});
    });
    await page.route("**/board/board/apps/example-app/panel/signals", async (route) => {
        signalReads.push(route.request().postDataJSON());
        await route.fulfill({ status, json: { items: [{ signal_uid: "signal-1" }], next_cursor: null } });
    });
    await page.route("**/board/board/apps/example-app/panel/interactions", async (route) => {
        interactionReads.push(route.request().postDataJSON());
        await route.fulfill({json:{items:[{interaction_uid:"B1234567890",state:"pending"}],next_cursor:null}});
    });
    await page.goto("/src/pages/BoardPage/components/apps/AppPanel.fixture.html");
    const panel = page.frameLocator("iframe");
    await expect(panel.locator("#result")).toHaveText("Ready");
    await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
    await panel.getByRole("button", { name: "Read", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("issue-1", { timeout: 7000 });
    expect(writes).toEqual([{ app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
    await panel.getByRole("button", { name: "Inject identity" }).click();
    await expect(panel.locator("#result")).toContainText("Denied", { timeout: 12000 });
    expect(writes).toHaveLength(1);
    await panel.getByRole("button", { name: "Signals", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("signal-1");
    expect(signalReads).toEqual([{ provider: "github", app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
    await panel.getByRole("button", {name:"Interactions",exact:true}).click();
    await expect(panel.locator("#result")).toContainText("B1234567890");
    expect(interactionReads).toEqual([{card_uid:"A1234567890",limit:10,app_revision:"a".repeat(64),binding_revision:"b".repeat(64)}]);
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
    expect(writes).toHaveLength(2);
    pending = false;
    for (const denial of [403, 404, 409]) {
        status = denial;
        await page.reload();
        await page.bringToFront();
        await expect(panel.locator("#result")).toHaveText("Ready");
        await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
        await panel.getByRole("button", { name: "Read", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("App panel unavailable", { timeout: 12000 });
        await expect(page.locator("iframe")).toHaveCount(0);
    }
    expect(writes).toHaveLength(5);
    for (const denial of [403, 404, 409]) {
        status = denial;
        await page.reload();
        await page.bringToFront();
        await expect(panel.locator("#result")).toHaveText("Ready");
        await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
        await panel.getByRole("button", { name: "Signals", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("App panel unavailable", { timeout: 12000 });
        await expect(page.locator("iframe")).toHaveCount(0);
    }
    expect(signalReads).toHaveLength(4);

    status = 200;
    await page.reload();
    await page.bringToFront();
    await expect(panel.locator("#result")).toHaveText("Ready");
    await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
    await panel.getByRole("button", { name: "Save draft" }).click();
    await expect
        .poll(async () => {
            await page.getByRole("button", { name: "Inspect sessions" }).click();
            return page.locator("output").textContent();
        })
        .toContain("preserved");
    await page.getByRole("button", { name: "Revoke panel sessions" }).click();
    await expect(page.getByRole("alert")).toContainText("App panel unavailable", { timeout: 12000 });
    await expect(page.locator("iframe")).toHaveCount(0);
    await page.getByRole("button", { name: "Inspect sessions" }).click();
    await expect(page.locator("output")).toHaveText(JSON.stringify({ state: null, active: 0 }));
    status = 500;
    await page.reload();
    await page.bringToFront();
    await expect(panel.locator("#result")).toHaveText("Ready");
    await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
    await panel.getByRole("button", { name: "Read", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("Denied", { timeout: 12000 });
    await expect(page.locator("iframe")).toHaveCount(1);
});
