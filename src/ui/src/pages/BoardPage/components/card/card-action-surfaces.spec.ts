import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
const titles = ["Signal evidence", "App resources", "App requests", "App execution"];
async function fixture(page: Page, query = "") {
    await page.route("**/card-action-surfaces-test.html*", (route) =>
        route.fulfill({
            contentType: "text/html",
            body: `<!doctype html>
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head><body><div id="root"></div>
        <script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => (type) => type; window.__vite_plugin_react_preamble_installed__ = true;</script>
        <script type="module" src="/src/pages/BoardPage/components/card/card-action-surfaces.fixture.tsx"></script></body></html>`,
        })
    );
    await page.goto(`/card-action-surfaces-test.html${query}`);
    await expect(page.getByRole("region", { name: "Native action card", exact: true })).toBeVisible();
}
async function managerCalls(page: Page) {
    return page.evaluate(() =>
        (window as unknown as { managerCalls: { url: string; method: string; data: unknown }[] }).managerCalls.filter((call) =>
            /\/signals|\/apps\/(resource|connections|interactions|executions)/.test(call.url)
        )
    );
}
async function actions(page: Page) {
    await page.getByRole("button", { name: "Actions", exact: true }).click();
}
async function close(page: Page, title: string) {
    await page.getByRole("dialog", { name: title, exact: true }).getByRole("button", { name: "Close", exact: true }).click();
}
for (const width of [390, 1440])
    test(`native managers only enter through Actions at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await fixture(page);
        await expect(page.getByRole("tablist")).toHaveCount(0);
        await expect(page.getByText("Past execution summaries", { exact: true })).toHaveCount(0);
        for (const title of titles) await expect(page.getByRole("button", { name: title, exact: true })).toHaveCount(0);
        expect(await managerCalls(page)).toEqual([]);
        await actions(page);
        for (const title of titles) await expect(page.getByRole("button", { name: title, exact: true })).toBeVisible();
        expect(await managerCalls(page)).toEqual([]);
        for (const title of titles) {
            await page.getByRole("button", { name: title, exact: true }).click();
            const dialog = page.getByRole("dialog", { name: title, exact: true });
            await expect(dialog).toBeVisible();
            if (title === "Signal evidence") await expect(dialog.getByText("Passed · #55")).toBeVisible();
            else if (title === "App resources") await expect(dialog.getByRole("combobox")).toBeEnabled();
            else if (title === "App requests") await expect(dialog.getByRole("radio", { name: "Approve", exact: true })).toBeVisible();
            else await expect(dialog.getByText("Permit issued", { exact: true })).toBeVisible();
            await expect
                .poll(() => dialog.evaluate((node) => getComputedStyle(node.parentElement!).transform))
                .toMatch(/^(none|matrix\(1, 0, 0, 1, 0, 0\))$/);
            await expect(dialog.getByRole("button", { name: title, exact: true })).toHaveCount(0);
            await page.screenshot({ path: `test-results/card-action-surfaces/${width}-${title.replaceAll(" ", "-")}.png` });
            await close(page, title);
            await expect(page.getByRole("dialog", { name: title, exact: true })).toHaveCount(0);
        }
        await page.getByRole("button", { name: "App resources", exact: true }).click();
        await page.getByRole("combobox").selectOption("connection");
        await expect(page.getByRole("checkbox", { name: "Example issue" })).toBeChecked();
        await page.getByRole("button", { name: "Clear selection", exact: true }).click();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByText("Selected resources: 0/20")).toBeVisible();
        await close(page, "App resources");
        await page.getByRole("button", { name: "App resources", exact: true }).click();
        await page.getByRole("combobox").selectOption("connection");
        await expect(page.getByRole("checkbox", { name: "Example issue" })).not.toBeChecked();
        await page.getByRole("checkbox", { name: "Example issue" }).check();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByText("Selected resources: 1/20")).toBeVisible();
        await close(page, "App resources");
        await page.getByRole("button", { name: "Signal evidence", exact: true }).click();
        await page.getByRole("button", { name: "Unlink", exact: true }).click();
        await expect(page.getByText("No linked evidence")).toBeVisible();
        await page.getByRole("button", { name: "Link a check", exact: true }).click();
        await page.getByRole("combobox", { name: "Repository" }).selectOption("resource");
        await page.getByRole("button", { name: "#55" }).click();
        await expect(page.getByText("Passed · #55")).toBeVisible();
        await close(page, "Signal evidence");
        await page.getByRole("button", { name: "App requests", exact: true }).click();
        await page.getByRole("radio", { name: "Approve", exact: true }).check();
        await page.getByRole("button", { name: "Submit response" }).click();
        await expect(page.getByText("Answered", { exact: true })).toBeVisible();
        await close(page, "App requests");
        await page.getByRole("button", { name: "App execution", exact: true }).click();
        await page.getByRole("button", { name: "Request stop", exact: true }).click();
        await page.getByRole("button", { name: "Request stop", exact: true }).last().click();
        await expect(page.getByText("Stop requested — awaiting runtime termination", { exact: true })).toBeVisible();
        await close(page, "App execution");
        const writes = (await managerCalls(page)).filter((call) => call.method !== "get");
        expect(writes.some((call) => call.url.endsWith("/responses"))).toBe(true);
        expect(writes.find((call) => call.url.endsWith("/stop"))?.data).toEqual({ generation: 7 });
        expect(writes.find((call) => call.url.endsWith("/unlink"))?.data).toEqual({ expected_revision: 5 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
test("readonly managers preserve visibility and mutation gates", async ({ page }) => {
    await fixture(page, "?readonly");
    await actions(page);
    await expect(page.getByRole("button", { name: "App resources", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Signal evidence", exact: true }).click();
    await expect(page.getByText("Passed · #55")).toBeVisible();
    await expect(page.getByRole("button", { name: "Unlink", exact: true })).toHaveCount(0);
    await close(page, "Signal evidence");
    await page.getByRole("button", { name: "App requests", exact: true }).click();
    await expect(page.getByRole("radio", { name: "Approve", exact: true })).toBeDisabled();
    await close(page, "App requests");
    await page.getByRole("button", { name: "App execution", exact: true }).click();
    await expect(page.getByRole("button", { name: "Request stop", exact: true })).toBeDisabled();
    expect((await managerCalls(page)).filter((call) => call.method !== "get")).toEqual([]);
});
test("manager failure recovers and identity switch closes manager", async ({ page }) => {
    await fixture(page, "?error");
    await actions(page);
    await page.getByRole("button", { name: "Signal evidence", exact: true }).click();
    await page.getByRole("button", { name: "Unlink", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await close(page, "Signal evidence");
    await page.getByRole("button", { name: "Signal evidence", exact: true }).click();
    await expect(page.getByText("Passed · #55")).toBeVisible();
    await page
        .getByRole("button", { name: "Switch identity", exact: true, includeHidden: true })
        .evaluate((button) => (button as HTMLButtonElement).click());
    await expect(page.getByRole("dialog", { name: "Signal evidence", exact: true })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Native action card", exact: true })).toBeVisible();
});

for (const width of [390, 1440])
    test(`card context is lazy and only accessible through Actions at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await fixture(page);
        await expect(page.getByRole("tablist")).toHaveCount(0);
        await expect(page.getByText("Past execution summaries", { exact: true })).toHaveCount(0);
        const receiptReads = () =>
            page.evaluate(
                () =>
                    (window as unknown as { managerCalls: { sections?: string }[] }).managerCalls.filter((call) =>
                        call.sections?.includes("execution_receipts")
                    ).length
            );
        expect(await receiptReads()).toBe(0);
        await actions(page);
        expect(await receiptReads()).toBe(0);
        for (const title of ["Execution", "Review", "History"]) {
            await page.getByRole("button", { name: title, exact: true }).click();
            const dialog = page.getByRole("dialog", { name: title, exact: true });
            await expect(dialog).toBeVisible();
            await expect(dialog.getByRole("status")).toBeVisible();
            if (title === "History") await expect(dialog.getByText("Preserved execution receipt", { exact: true })).toBeVisible();
            await close(page, title);
            await expect(page.getByRole("region", { name: "Native action card", exact: true })).toBeVisible();
        }
        expect(await receiptReads()).toBeGreaterThan(0);
        await expect(page.getByText("Preserved execution receipt", { exact: true })).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

for (const width of [320, 390])
    test(`agent identity is on demand and distinguishes attestation from liveness at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await fixture(page);
        const agentReads = () =>
            page.evaluate(
                () =>
                    (window as unknown as { managerCalls: { sections?: string }[] }).managerCalls.filter((call) =>
                        call.sections?.includes("agent_execution")
                    ).length
            );
        expect(await agentReads()).toBe(0);
        await actions(page);
        expect(await agentReads()).toBe(0);
        for (const title of ["Review", "History"]) {
            await page.getByRole("button", { name: title, exact: true }).click();
            await expect(page.getByRole("dialog", { name: title, exact: true })).toBeVisible();
            expect(await agentReads()).toBe(0);
            await close(page, title);
        }
        await page.getByRole("button", { name: "Execution", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Execution", exact: true });
        const agent = dialog.getByRole("region", { name: "Agent lifecycle", exact: true });
        await expect(agent).toContainText("App-reported start: sample-agent");
        await expect(agent).toContainText("start-opaque");
        await expect(agent).toContainText("Authorized at last check");
        await expect(agent).toContainText("Process liveness: Unknown");
        await expect(dialog.getByText("never-render-raw-runtime")).toHaveCount(0);
        expect(await agentReads()).toBe(1);
        await expect(dialog.locator("..")).toHaveCSS("opacity", "1");
        await page.screenshot({ path: `/private/tmp/langboard-agent-execution-${width}.png`, animations: "disabled" });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.evaluate(() => (window as unknown as { revokeAgentReport: () => void }).revokeAgentReport());
        await dialog.getByRole("button", { name: "Refresh execution", exact: true }).click();
        await expect(agent).toHaveCount(0);
        await expect(dialog.getByText("Agent lifecycle: Unknown", { exact: true })).toBeVisible();
        await expect(dialog.getByRole("button", { name: "Refresh execution", exact: true })).toBeEnabled();
        await expect(agent).toHaveCount(0);
        expect(await agentReads()).toBe(2);
        await close(page, "Execution");
    });

for (const query of ["?no-agent", "?old-generation", "?private"])
    test(`missing, old or private agent evidence stays unknown ${query}`, async ({ page }) => {
        await fixture(page, query);
        await actions(page);
        await page.getByRole("button", { name: "Execution", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Execution", exact: true });
        await expect(dialog.getByText("Agent lifecycle: Unknown", { exact: true })).toBeVisible();
        await expect(dialog.getByText("sample-agent")).toHaveCount(0);
    });

test("agent authorization snapshot disappears at expiry without renewed requests", async ({ page }) => {
    await page.clock.install();
    await fixture(page);
    await actions(page);
    await page.getByRole("button", { name: "Execution", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Execution", exact: true });
    await expect(dialog.getByRole("region", { name: "Agent lifecycle", exact: true })).toBeVisible();
    await page.clock.fastForward(61_000);
    await expect(dialog.getByText("Agent lifecycle: Unknown", { exact: true })).toBeVisible();
    await expect(dialog.getByText("sample-agent")).toHaveCount(0);
    const requests = await page.evaluate(
        () =>
            (window as unknown as { managerCalls: { sections?: string }[] }).managerCalls.filter((call) => call.sections?.includes("agent_execution"))
                .length
    );
    expect(requests).toBe(1);
});

type PrivateUIWindow = Window & {
    privateUI: {
        classifyColleague: (value: string) => void;
        setMembers: (value: string) => void;
        setVisibility: (value: string) => void;
        snapshot: () => { visibility: string };
    };
    managerCalls: { method: string; url: string }[];
};
async function privateUIFixture(page: Page) {
    await page.route("**/private-ui-wave6.html*", (route) =>
        route.fulfill({
            contentType: "text/html",
            body: `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head>
                <body><div id="root"></div><script type="module">
                import RefreshRuntime from "/@react-refresh";
                RefreshRuntime.injectIntoGlobalHook(window);
                window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
                window.__vite_plugin_react_preamble_installed__=true;</script>
                <script type="module" src="/src/pages/BoardPage/components/card/card-action-surfaces.fixture.tsx"></script>
                </body></html>`,
        })
    );
    await page.goto("/private-ui-wave6.html?private-ui");
    await expect(page.getByRole("region", { name: "Native action card", exact: true })).toBeVisible();
    await expect(page.getByText("Native private card body", { exact: true })).toBeVisible();
}
async function privateUITransition(page: Page, method: "setMembers" | "setVisibility" | "classifyColleague", value: string) {
    await page.evaluate(({ method, value }) => (window as unknown as PrivateUIWindow).privateUI[method](value), { method, value });
}
for (const width of [390, 1440]) {
    test(`native detail privacy surface follows retained member classification at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        await privateUIFixture(page);
        const surface = page.locator("[data-card-surface]");
        await expect(surface).toHaveCount(1);
        await expect(surface).toHaveAttribute("data-card-privacy", "private");
        await privateUITransition(page, "setMembers", "internal");
        await privateUITransition(page, "setVisibility", "INTERNAL");
        await expect(surface).not.toHaveAttribute("data-card-privacy");
        for (const classification of ["external", "internal", "external"]) {
            await privateUITransition(page, "classifyColleague", classification);
            if (classification === "external") await expect(surface).toHaveAttribute("data-card-privacy", "whisper");
            else await expect(surface).not.toHaveAttribute("data-card-privacy");
            expect(await page.evaluate(() => (window as unknown as PrivateUIWindow).privateUI.snapshot())).toEqual({ visibility: "INTERNAL" });
        }
        await privateUITransition(page, "setVisibility", "PRIVATE");
        await privateUITransition(page, "classifyColleague", "internal");
        await expect(surface).toHaveAttribute("data-card-privacy", "private");
        const writes = await page.evaluate(() =>
            (window as unknown as PrivateUIWindow).managerCalls.filter((call) => call.method !== "get" && !call.url.endsWith("/seen"))
        );
        expect(writes).toEqual([]);
    });
}
for (const width of [390, 1440])
    test(`native private controls remain hidden through member changes at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        await privateUIFixture(page);
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        for (const membership of ["solo", "internal", "external", "solo"]) {
            await privateUITransition(page, "setMembers", membership);
            await expect(page.locator("[data-card-presentation='visibility.private']")).toContainText("🔐Private");
            await expect(page.getByRole("button", { name: "Assign members", exact: true })).toHaveCount(0);
            await expect(page.getByText("Members", { exact: true })).toHaveCount(0);
            await expect(page.getByRole("button", { name: "Change card visibility", exact: true })).toHaveCount(0);
            await expect(page.getByRole("button", { name: "Whispers", exact: true })).toHaveCount(0);
            expect(await page.evaluate(() => (window as unknown as PrivateUIWindow).privateUI.snapshot())).toEqual({ visibility: "PRIVATE" });
        }
        await privateUITransition(page, "setVisibility", "INTERNAL");
        await expect(page.getByText("Members", { exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Whispers", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Assign members", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Change card visibility", exact: true })).toBeVisible();
        await privateUITransition(page, "setVisibility", "PRIVATE");
        await expect(page.getByText("Members", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Assign members", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Change card visibility", exact: true })).toHaveCount(0);
        const writes = await page.evaluate(() =>
            (window as unknown as PrivateUIWindow).managerCalls.filter((c) => c.method !== "get" && !c.url.endsWith("/seen"))
        );
        expect(writes).toEqual([]);
    });

// Uses the real Plate editor and mention popup; network responses remain local fixtures.
test("native new-comment mention popup removes other people on current visibility changes", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await privateUIFixture(page);
    await privateUITransition(page, "setMembers", "external");
    await page.getByRole("button", { name: "Comments", exact: true }).click();
    await page.getByRole("button", { name: /Add a comment as/ }).click();
    const editor = page.locator("[data-slate-editor='true'][contenteditable='true']").first();
    await expect(editor).toBeVisible();
    await editor.click();
    await page.keyboard.press("End");
    await page.keyboard.type("@");
    await expect(page.getByRole("combobox", { expanded: true })).toBeVisible();
    await expect(page.getByRole("option")).toHaveCount(0);
    await privateUITransition(page, "setVisibility", "INTERNAL");
    await expect(page.getByRole("option").filter({ hasText: "colleague" })).toBeVisible();
    await expect(page.getByRole("option").filter({ hasText: "outsider" })).toBeVisible();
    await privateUITransition(page, "setVisibility", "PRIVATE");
    await expect(page.getByRole("combobox", { expanded: true })).toBeVisible();
    await expect(page.getByRole("option")).toHaveCount(0);
});
