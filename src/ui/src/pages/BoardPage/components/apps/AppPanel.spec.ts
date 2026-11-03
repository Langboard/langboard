import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
const sdk = readFileSync("node_modules/@langboard/app-panel/index.mjs", "utf8");
test("independent resource panel pins authority, rejects identity injection and cancels on close", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    const resourcePage = {
        items: Array.from({ length: 25 }, (_, index) => ({
            resource_uid: `R${String(index + 1).padStart(10, "0")}`,
            connection_uid: `C${String((index % 2) + 1).padStart(10, "0")}`,
            binding_uid: "B0000000001",
            app_key: "example-app",
            resource_type: "workflow",
            external_resource_id: "erp:v1:00000000-0000-0000-0000-000000000001:cash_allocation_batch:9a203e89-c1a8-42d8-95d0-f6a0a8b7bafa",
            external_resource_id_truncated: false,
            name: "가".repeat(200),
            access_revision: 1,
            binding_revision: "b".repeat(64),
            health: "unknown",
        })),
        next_cursor: "AQAAAAAAAAAqAAAAAAAAABk",
        limit: 25,
    };
    const responseBytes = Buffer.byteLength(JSON.stringify({ type: "response", version: 1, id: 1, result: resourcePage }));
    expect(responseBytes).toBeGreaterThan(16 * 1024);
    expect(responseBytes).toBeLessThan(128 * 1024);
    const writes: unknown[] = [];
    const signalReads: unknown[] = [];
    const interactionReads: unknown[] = [];
    const humanWrites: unknown[] = [];
    let humanAnswered = false;
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
        <button id="open-interaction">Open request</button>
        <button id="inject-answer">Inject answer</button><button id="interactions">Interactions</button>
        <button id="signals">Signals</button><button id="save">Save draft</button>
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
        document.querySelector('#open-interaction').onclick = async () => {
            output.textContent = JSON.stringify(await session.request('interactions.open', {card_uid:'A1234567890',interaction_uid:'B1234567890'}));
        };
        document.querySelector('#inject-answer').onclick = async () => {
            try { await session.request('interactions.open', {card_uid:'A1234567890',interaction_uid:'B1234567890',values:{approve:true}}); }
            catch(error) {output.textContent = 'Denied: ' + error.message;}
        };
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
        await r.fulfill({ status, json: resourcePage }).catch(() => {});
    });
    await page.route("**/board/board/apps/example-app/panel/signals", async (route) => {
        signalReads.push(route.request().postDataJSON());
        await route.fulfill({ status, json: { items: [{ signal_uid: "signal-1" }], next_cursor: null } });
    });
    await page.route("**/board/board/apps/example-app/panel/interactions", async (route) => {
        interactionReads.push(route.request().postDataJSON());
        await route.fulfill({
            json: {
                items: [
                    {
                        interaction_uid: "B1234567890",
                        revision: humanAnswered ? 2 : 1,
                        state: humanAnswered ? "answered" : "pending",
                        source: { app_key: "example-app", connection_uid: "C1234567890" },
                        request: {
                            kind: "question",
                            prompt: { en: "Choose next action" },
                            questions: [
                                {
                                    id: "action",
                                    prompt: { en: "Action" },
                                    type: "single_choice",
                                    choices: [{ id: "review", label: { en: "Review" } }],
                                },
                            ],
                            presentation: { type: "choice_card" },
                            expires_at: new Date(Date.now() + 3600000).toISOString(),
                        },
                        response: humanAnswered
                            ? { values: { action: { choice_id: "review" } }, actor_uid: "actor", response_uid: "D1234567890" }
                            : null,
                    },
                ],
                next_cursor: null,
                card_revision: 12,
            },
        });
    });
    await page.route("**/panel/cards/A1234567890/interactions/B1234567890/responses", async (route) => {
        humanWrites.push(route.request().postDataJSON());
        humanAnswered = true;
        await route.fulfill({ status: 500, json: { detail: "Response lost after commit" } });
    });
    await page.goto("/src/pages/BoardPage/components/apps/AppPanel.fixture.html");
    const panel = page.frameLocator("iframe");
    await expect(panel.locator("#result")).toHaveText("Ready");
    await expect(page.locator("iframe")).toHaveAttribute("title", "Independent resources");
    await expect(page.locator("iframe")).toHaveAttribute("sandbox", "allow-scripts allow-forms");
    await expect(page.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
    await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
    await panel.getByRole("button", { name: "Read", exact: true }).click();
    await expect(panel.locator("#result")).toHaveText(JSON.stringify(resourcePage), { timeout: 7000 });
    expect(writes).toEqual([{ app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
    await panel.getByRole("button", { name: "Inject identity" }).click();
    await expect(panel.locator("#result")).toContainText("Denied", { timeout: 12000 });
    expect(writes).toHaveLength(1);
    await panel.getByRole("button", { name: "Signals", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("signal-1");
    expect(signalReads).toEqual([{ provider: "github", app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
    await panel.getByRole("button", { name: "Interactions", exact: true }).click();
    await expect(panel.locator("#result")).toContainText("B1234567890");
    expect(interactionReads).toEqual([{ card_uid: "A1234567890", limit: 10, app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
    await panel.getByRole("button", { name: "Inject answer" }).click();
    await expect(panel.locator("#result")).toContainText("Denied");
    expect(humanWrites).toHaveLength(0);
    await panel.getByRole("button", { name: "Open request" }).click();
    await expect(panel.locator("#result")).toContainText(JSON.stringify({ state: "opened" }).slice(1, -1));
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Choose next action")).toBeVisible();
    expect(humanWrites).toHaveLength(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: ".verification/panel-human-response.png", fullPage: true });
    await dialog.getByRole("radio", { name: "Review", exact: true }).check();
    await dialog.getByRole("button", { name: "Submit response", exact: true }).click();
    await expect(dialog.getByText("Answered", { exact: true })).toBeVisible();
    expect(humanWrites).toHaveLength(1);
    expect(humanWrites[0]).toMatchObject({
        expected_revision: 1,
        values: { action: { choice_id: "review" } },
        app_revision: "a".repeat(64),
        binding_revision: "b".repeat(64),
    });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
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

for (const appKey of ["example-app", "second-app"])
    for (const width of [390, 1440]) {
        test(`${appKey} opens human runtime dialog without automatic writes at ${width}`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            const controls: unknown[] = [];
            await page.route("**/panel-sdk-resources.json", (r) =>
                r.fulfill({ json: { version: 1, module_url: "/widgets.mjs", css_url: "/widgets.css" } })
            );
            await page.route(`**/board/board/apps/${appKey}/panel`, (r) =>
                r.fulfill({
                    json: {
                        key: appKey,
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
                    body: `<button id="open" disabled>Runtime controls</button><pre id="result"></pre>
                    <script type="module">
                    import {connectPanel} from './sdk.mjs';
                    const session = await connectPanel({expectedHostOrigin:'http://127.0.0.1:4181'});
                    const output = document.querySelector('#result');
                    document.querySelector('#open').onclick = async () => {
                        output.textContent = 'requesting';
                        try { output.textContent = JSON.stringify(await session.request('runtimes.open', {card_uid:'A1234567890'})); }
                        catch (error) { output.textContent = String(error); }
                    };
                    document.querySelector('#open').disabled = false;
                    </script>`,
                })
            );
            let state = "authorized";
            let runtimeStatus = 200;
            let nextRequest: string | null = null;
            const newControls: unknown[] = [];
            const revision = "2026-10-11T15:00:00.123456+00:00";
            await page.route("**/panel/runtimes", (r) =>
                r.fulfill({
                    status: runtimeStatus,
                    json: {
                        items: [
                            {
                                request_uid: "B1234567890",
                                app_key: appKey,
                                generation: 7,
                                state,
                                current_generation: nextRequest ? 8 : 7,
                                current_card_revision: revision,
                                new_run_available: state === "stopped" && !nextRequest,
                                resume_request_uid: nextRequest,
                                followup_action: nextRequest ? "new_run_requested" : null,
                            },
                        ],
                        next_cursor: null,
                    },
                })
            );
            await page.route("**/panel/cards/A1234567890/runtimes/B1234567890/stop", (r) => {
                controls.push(r.request().postDataJSON());
                state = "stop_requested";
                return r.fulfill({ json: { state, execution_resumed: false } });
            });
            await page.route("**/panel/cards/A1234567890/runtimes/B1234567890/new", async (r) => {
                newControls.push(r.request().postDataJSON());
                nextRequest = "C1234567890";
                await r.fulfill({ json: { state: "new_run_requested", execution_resumed: false, started: false } });
            });
            await page.goto(`/src/pages/BoardPage/components/apps/AppPanel.fixture.html?app=${appKey}`);
            await expect(page.getByRole("region", { name: "Independent resources" })).toBeVisible();
            await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
            const openControls = page.frameLocator("iframe").getByRole("button", { name: "Runtime controls" });
            await expect(openControls).toBeEnabled();
            await expect(page.getByRole("region", { name: "Independent resources" }).getByRole("status")).toHaveCount(0);
            await openControls.click();
            await expect(page.frameLocator("iframe").locator("#result")).toContainText("opened");
            await expect(page.getByRole("dialog")).toBeVisible();
            await expect(page.getByText("Permit issued", { exact: true })).toBeVisible();
            expect(controls).toHaveLength(0);
            await page.getByRole("button", { name: "Request stop", exact: true }).click();
            expect(controls).toHaveLength(0);
            await page.getByRole("button", { name: "Request stop", exact: true }).last().click();
            await expect(page.getByText("Stop requested — awaiting runtime termination", { exact: true })).toBeVisible();
            expect(controls).toEqual([{ generation: 7, app_revision: "a".repeat(64), binding_revision: "b".repeat(64) }]);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await page.screenshot({ path: `.verification/panel-runtime-${appKey}-${width}.png`, fullPage: true });
            state = "stopped";
            await page.getByRole("button", { name: "Refresh", exact: true }).click();
            await expect(page.getByRole("button", { name: "Request new run", exact: true })).toBeVisible();
            expect(newControls).toHaveLength(0);
            await page.getByRole("button", { name: "Request new run", exact: true }).click();
            expect(newControls).toHaveLength(0);
            await page.getByRole("button", { name: "Request new run", exact: true }).last().click();
            await expect(page.getByRole("button", { name: "Request new run", exact: true })).toHaveCount(0);
            expect(newControls).toEqual([
                {
                    generation: 7,
                    expected_generation: 7,
                    expected_revision: revision,
                    app_revision: "a".repeat(64),
                    binding_revision: "b".repeat(64),
                },
            ]);
            await expect(page.getByText("Runtime reported stopped", { exact: true })).toBeVisible();
            runtimeStatus = 403;
            await page.getByRole("button", { name: "Refresh", exact: true }).click();
            await expect(page.getByRole("dialog")).toHaveCount(0);
            await expect(page.locator("iframe")).toHaveCount(0);
            await expect(page.getByRole("alert")).toContainText("App panel unavailable");
        });
    }

for (const initial of [503, "offline"] as const)
    test(`panel retry rechecks authority after ${initial} and never restores a revoked frame`, async ({ page }) => {
        let status: number | "offline" = initial;
        let reads = 0;
        let frames = 0;
        const methods: string[] = [];
        await page.route("**/panel-sdk-resources.json", (route) =>
            route.fulfill({ json: { version: 1, module_url: "/widgets.mjs", css_url: "/widgets.css" } })
        );
        await page.route("**/board/board/apps/example-app/panel", (route) => {
            reads++;
            methods.push(route.request().method());
            if (status === "offline") return route.abort("internetdisconnected");
            return route.fulfill({
                status,
                json:
                    status === 200
                        ? {
                              key: "example-app",
                              version: "1.0.0",
                              panel: { url: "https://panel.example.invalid/panel", name: "Independent resources" },
                              app_revision: "a".repeat(64),
                              binding_revision: "b".repeat(64),
                          }
                        : { code: "unavailable" },
            });
        });
        await page.route("https://panel.example.invalid/sdk.mjs", (route) =>
            route.fulfill({ body: sdk, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" } })
        );
        await page.route("https://panel.example.invalid/panel", (route) => {
            frames++;
            return route.fulfill({
                contentType: "text/html",
                body: `<pre id="result">Connecting</pre><script type="module">
            import {connectPanel} from './sdk.mjs';
            await connectPanel({expectedHostOrigin:'http://127.0.0.1:4181',onShow:()=>{},onDispose:()=>{}});
            document.querySelector('#result').textContent='Ready';</script>`,
            });
        });
        await page.goto("/src/pages/BoardPage/components/apps/AppPanel.fixture.html");
        await expect(page.getByRole("alert")).toContainText("App panel unavailable");
        await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
        expect(reads).toBe(1);
        expect(frames).toBe(0);
        status = 403;
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect.poll(() => reads).toBe(2);
        await expect(page.getByRole("alert")).toContainText("App panel unavailable");
        await expect(page.locator("iframe")).toHaveCount(0);
        status = 200;
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.frameLocator("iframe").locator("#result")).toHaveText("Ready");
        expect(reads).toBe(3);
        expect(frames).toBe(1);
        await page.getByRole("button", { name: "Revoke panel sessions", exact: true }).click();
        await expect(page.locator("iframe")).toHaveCount(0);
        status = 403;
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect.poll(() => reads).toBe(4);
        await expect(page.getByRole("alert")).toContainText("App panel unavailable");
        expect(frames).toBe(1);
        expect(methods).toEqual(["GET", "GET", "GET", "GET"]);
    });
