import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
test.beforeEach(async ({ page }) => {
    await page.route("**/apps/installation-requests?*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
    await page.route("**/apps/catalog*", (route) => route.fulfill({ json: { official } }));
});
const revision = "a".repeat(64);
const installed = {
    declaration: {
        key: "github-panel",
        name: "GitHub",
        version: "0.1.1",
        description: "Checks and commits in your board",
        capabilities: ["panels.render", "signals.read"],
    },
    revision,
    is_enabled: true,
    installation: { kind: "official", sha256: "b".repeat(64), store: { icon: "panel/icon-light.svg", icon_dark: "panel/icon-dark.svg" } },
};
const official = [{ key: "github-panel", name: "GitHub", description: "Checks and commits in your board", repository: "publisher/github-app" }];
const manualZIP = readFileSync(join(process.cwd(), "src/pages/SettingsPage/fixtures/manual-panel.zip"));
const manual = {
    ...installed,
    declaration: { ...installed.declaration, key: "example-panel", name: "Example panel" },
    installation: { kind: "manual" },
};

for (const width of [1440, 390]) {
    test(`manual ZIP create and revision update clear the file picker at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved: typeof manual | undefined;
        let writes = 0;
        await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official: [], installed: saved ? [saved] : [] } }));
        await page.route("**/settings/apps/packages/upload", async (route) => {
            const contentType = route.request().headers()["content-type"];
            expect(contentType).toContain("multipart/form-data; boundary=");
            const body = await new Response(new Uint8Array(route.request().postDataBuffer()!), {
                headers: { "Content-Type": contentType },
            }).formData();
            const file = body.get("file") as File;
            expect(file.name).toBe("manual-panel.zip");
            expect(Buffer.from(await file.arrayBuffer())).toEqual(manualZIP);
            expect(body.get("expected_revision")).toBe(saved?.revision ?? null);
            writes++;
            saved = { ...manual, revision: (writes === 1 ? "a" : "b").repeat(64) };
            await route.fulfill({ json: { app: saved } });
        });
        await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
        await page.getByRole("button", { name: "Manual ZIP installation", exact: true }).click();
        const picker = page.getByLabel("App ZIP package", { exact: true });
        const installZIP = page.getByRole("button", { name: "Install ZIP", exact: true });
        await picker.setInputFiles({ name: "manual-panel.zip", mimeType: "application/zip", buffer: manualZIP });
        expect(writes).toBe(0);
        await installZIP.click();
        await expect(installZIP).toBeDisabled();
        await expect.poll(() => writes).toBe(1);
        await expect(picker).toHaveValue("");
        await page.getByLabel("Create or update", { exact: true }).selectOption("example-panel");
        await picker.setInputFiles({ name: "manual-panel.zip", mimeType: "application/zip", buffer: manualZIP });
        await installZIP.click();
        await expect.poll(() => writes).toBe(2);
        await expect(picker).toHaveValue("");
        await expect(installZIP).toBeDisabled();
        await page.getByRole("button", { name: "Manual", exact: true }).click();
        await expect(page.getByRole("button", { name: "App details: Example panel" })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("closing manual installation discards the hidden file selection", async ({ page }) => {
    await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official: [], installed: [] } }));
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
    const toggle = page.getByRole("button", { name: "Manual ZIP installation", exact: true });
    await toggle.click();
    await page
        .getByLabel("App ZIP package", { exact: true })
        .setInputFiles({ name: "manual-panel.zip", mimeType: "application/zip", buffer: manualZIP });
    await expect(page.getByRole("button", { name: "Install ZIP", exact: true })).toBeEnabled();
    await toggle.click();
    await toggle.click();
    await expect(page.getByLabel("App ZIP package", { exact: true })).toHaveValue("");
    await expect(page.getByRole("button", { name: "Install ZIP", exact: true })).toBeDisabled();
});

test("empty or oversized ZIPs are rejected before any installation request", async ({ page }) => {
    let writes = 0;
    await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official: [], installed: [] } }));
    await page.route("**/settings/apps/packages/upload", (route) => {
        writes++;
        return route.fulfill({ json: {} });
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
    await page.getByRole("button", { name: "Manual ZIP installation", exact: true }).click();
    const picker = page.getByLabel("App ZIP package", { exact: true });
    const install = page.getByRole("button", { name: "Install ZIP", exact: true });
    for (const size of [0, 8 * 1024 * 1024 + 1]) {
        await picker.setInputFiles({ name: "invalid.zip", mimeType: "application/zip", buffer: Buffer.alloc(size) });
        await expect(picker).toHaveAttribute("aria-invalid", "true");
        await expect(install).toBeDisabled();
        await expect(page.getByText("Choose a nonempty ZIP file up to 8 MiB.")).toBeVisible();
        await expect(page.getByRole("alert")).toHaveCount(0);
    }
    await picker.setInputFiles({ name: "manual-panel.zip", mimeType: "application/zip", buffer: manualZIP });
    await expect(install).toBeEnabled();
    await expect(picker).toHaveAttribute("aria-invalid", "false");
    expect(writes).toBe(0);
});

for (const failure of ["conflict", "lost-response"] as const) {
    test(`manual ZIP ${failure} requires explicit current readback before another write`, async ({ page }) => {
        let saved: typeof manual | undefined = failure === "conflict" ? manual : undefined;
        let writes = 0;
        let reads = 0;
        await page.route("**/settings/apps/packages", (route) => {
            reads++;
            return route.fulfill({ json: { official: [], installed: saved ? [saved] : [] } });
        });
        await page.route("**/settings/apps/packages/upload", async (route) => {
            const contentType = route.request().headers()["content-type"];
            const body = await new Response(new Uint8Array(route.request().postDataBuffer()!), {
                headers: { "Content-Type": contentType },
            }).formData();
            expect(body.get("expected_revision")).toBe(saved?.revision ?? null);
            writes++;
            saved = { ...manual, revision: (writes === 1 ? "b" : "c").repeat(64) };
            if (writes === 1) return failure === "conflict" ? route.fulfill({ status: 409, json: {} }) : route.abort("internetdisconnected");
            return route.fulfill({ json: { app: saved } });
        });
        await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
        await page.getByRole("button", { name: "Manual ZIP installation", exact: true }).click();
        if (saved) await page.getByLabel("Create or update", { exact: true }).selectOption("example-panel");
        const picker = page.getByLabel("App ZIP package", { exact: true });
        const install = page.getByRole("button", { name: "Install ZIP", exact: true });
        await picker.setInputFiles({ name: "manual-panel.zip", mimeType: "application/zip", buffer: manualZIP });
        await install.click();
        await expect(page.getByRole("alert")).toContainText("Installation could not be confirmed");
        await expect(install).toBeDisabled();
        await expect(picker).toBeDisabled();
        expect(writes).toBe(1);
        expect(reads).toBe(1);
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await expect(install).toBeEnabled();
        expect(reads).toBe(2);
        expect(writes).toBe(1);
        await page.getByLabel("Create or update", { exact: true }).selectOption("example-panel");
        await install.click();
        await expect.poll(() => writes).toBe(2);
        await expect(picker).toHaveValue("");
        await expect(install).toBeDisabled();
    });
}
for (const width of [1440, 390]) {
    test(`official install receipt and mobile-safe detail at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved = false;
        let writes = 0;
        await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official, installed: saved ? [installed] : [] } }));
        await page.route("**/settings/apps/packages/official/github-panel/install", async (route) => {
            writes++;
            expect(route.request().postDataJSON()).toEqual({ expected_revision: null });
            saved = true;
            await route.fulfill({ json: { app: installed } });
        });
        await page.route("**/apps/packages/**/panel/icon-*.svg", (route) =>
            route.fulfill({
                contentType: "image/svg+xml",
                body: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
                    <circle cx="10" cy="10" r="8" fill="${route.request().url().endsWith("icon-dark.svg") ? "white" : "black"}"/></svg>`,
            })
        );
        await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
        await page.getByRole("button", { name: "Download and install", exact: true }).click();
        await expect(page.getByRole("button", { name: "Update", exact: true })).toBeVisible();
        expect(writes).toBe(1);
        await page.getByRole("button", { name: "App details: GitHub" }).click();
        await expect(page.getByRole("heading", { name: "Permissions" })).toBeVisible();
        await expect(page.getByText("signals.read", { exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.evaluate(() => document.documentElement.classList.add("dark"));
        await expect(page.locator("img[src$='icon-dark.svg']")).toBeVisible();
        await expect(page.locator("img[src$='icon-light.svg']")).toBeHidden();
        await page.screenshot({ path: `.verification/app-store-detail-dark-${width}.png`, fullPage: true });
        await page.evaluate(() => document.documentElement.classList.remove("dark"));
        await expect(page.locator("img[src$='icon-light.svg']")).toBeVisible();
        await page.screenshot({ path: `.verification/app-store-detail-${width}.png`, fullPage: true });
    });
}
test("uncertain install locks writes until current readback", async ({ page }) => {
    let writes = 0;
    await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official, installed: [] } }));
    await page.route("**/settings/apps/packages/official/github-panel/install", async (route) => {
        writes++;
        await route.fulfill({ json: { app: { ...installed, revision: "invalid" } } });
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
    await page.getByRole("button", { name: "Download and install", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download and install", exact: true })).toBeDisabled();
    expect(writes).toBe(1);
});
test("ordinary board member cannot install centrally", async ({ page }) => {
    let requests = 0;
    await page.route("**/settings/apps/packages**", async (route) => {
        requests++;
        await route.fulfill({ json: {} });
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html?owner");
    await expect(page.getByRole("button", { name: "Request installation: GitHub", exact: true })).toBeVisible();
    expect(requests).toBe(0);
});

// Supply files extracted from SHA-256-verified official release ZIPs.
const officialPackageRoot = process.env.LANGBOARD_OFFICIAL_PACKAGE_ROOT;
for (const app of ["github", "glitchtip", "dokploy"]) {
    for (const width of [1440, 390]) {
        test(`published ${app} package icons load in both themes at ${width}`, async ({ page }) => {
            test.skip(!officialPackageRoot, "Requires verified official release package assets");
            const packageRoot = join(officialPackageRoot!, app);
            const manifest = JSON.parse(readFileSync(join(packageRoot, "manifest.json"), "utf8"));
            const receipt = { ...installed, declaration: manifest.declaration, installation: { ...installed.installation, store: manifest.store } };
            await page.setViewportSize({ width, height: 900 });
            await page.route("**/settings/apps/packages", (route) =>
                route.fulfill({
                    json: {
                        official: [
                            {
                                key: manifest.declaration.key,
                                name: manifest.declaration.name,
                                description: manifest.declaration.description,
                                repository: `yamonco/langboard-app-${app}`,
                            },
                        ],
                        installed: [receipt],
                    },
                })
            );
            await page.route("**/apps/packages/**/panel/icon-*.svg", (route) => {
                const asset = new URL(route.request().url()).pathname.split("/").pop()!;
                return route.fulfill({ contentType: "image/svg+xml", body: readFileSync(join(packageRoot, "panel", asset)) });
            });
            await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
            await page.getByRole("button", { name: `App details: ${manifest.declaration.name}` }).click();
            for (const dark of [false, true, false]) {
                await page.evaluate((enabled) => document.documentElement.classList.toggle("dark", enabled), dark);
                const active = page.locator(`img[src$="icon-${dark ? "dark" : "light"}.svg"]:visible`);
                await expect(active).toHaveCount(1);
                await expect
                    .poll(() => active.evaluate((image) => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))
                    .toBe(true);
                await expect(page.locator(`img[src$="icon-${dark ? "light" : "dark"}.svg"]`)).toBeHidden();
                expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
                await page.screenshot({ path: `.verification/published-${app}-${dark ? "dark" : "light"}-${width}.png`, fullPage: true });
            }
        });
    }
}

const requestReceipt = {
    uid: "ABCDEFGHIJK",
    requester_uid: "qa",
    project_uid: "PROJECT0001",
    source: "official",
    app_key: "github-panel",
    name: "GitHub",
    version: "1.2.3",
    sha256: "d".repeat(64),
    size: 2048,
    provenance: { repository: "publisher/github-app", tag: "v1.2.3" },
    expected_app_revision: null,
    decision: "pending",
    installation_status: "not_started",
    installation_error: null,
    reason: null,
    revision,
};
for (const width of [1440, 390]) {
    test(`member official and manual requests preserve context without board grants at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let saved: typeof requestReceipt | undefined;
        let boardWrites = 0;
        let requestWrites = 0;
        await page.route("**/board/**", (route) => {
            if (route.request().method() === "GET") return route.continue();
            boardWrites++;
            return route.abort();
        });
        await page.route("**/apps/installation-requests?*", (route) => route.fulfill({ json: { items: saved ? [saved] : [], next_cursor: null } }));
        await page.route("**/apps/installation-requests/ABCDEFGHIJK", (route) => route.fulfill({ json: { request: saved } }));
        await page.route("**/apps/installation-requests/official/github-panel", async (route) => {
            const body = route.request().postDataJSON();
            expect(body.project_uid).toBe("PROJECT0001");
            expect(body.request_key).toMatch(/^[0-9a-f-]{36}$/);
            saved = { ...requestReceipt };
            requestWrites++;
            await route.fulfill({ json: { request: saved } });
        });
        await page.route("**/apps/installation-requests/upload", async (route) => {
            const form = await new Response(new Uint8Array(route.request().postDataBuffer()!), {
                headers: { "Content-Type": route.request().headers()["content-type"] },
            }).formData();
            expect(form.get("project_uid")).toBe("PROJECT0001");
            expect(form.get("request_key")).toMatch(/^[0-9a-f-]{36}$/);
            expect(Buffer.from(await (form.get("file") as File).arrayBuffer())).toEqual(manualZIP);
            saved = { ...requestReceipt, source: "manual" };
            requestWrites++;
            await route.fulfill({ json: { request: saved } });
        });
        await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html?owner&project_uid=PROJECT0001");
        await page.getByRole("button", { name: "Request installation: GitHub", exact: true }).click();
        await expect(page.getByRole("article", { name: "Staged package identity" })).toContainText("d".repeat(64));
        await expect(page.getByRole("article", { name: "Staged package identity" })).toContainText("Pending review · Installation not started");
        await page
            .getByLabel("App ZIP package", { exact: true })
            .setInputFiles({ name: "manual-panel.zip", mimeType: "application/zip", buffer: manualZIP });
        await page.getByRole("button", { name: "Request ZIP installation", exact: true }).click();
        await expect(page.getByLabel("App ZIP package", { exact: true })).toHaveValue("");
        expect(requestWrites).toBe(2);
        expect(boardWrites).toBe(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("ambiguous request reads back and reuses its durable key across reload", async ({ page }) => {
    const keys: string[] = [];
    let reads = 0;
    await page.route("**/apps/installation-requests?*", (route) => {
        reads++;
        return route.fulfill({ json: { items: [], next_cursor: null } });
    });
    await page.route("**/apps/installation-requests/official/github-panel", async (route) => {
        keys.push(route.request().postDataJSON().request_key);
        await route.abort("failed");
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html?owner");
    const submit = page.getByRole("button", { name: "Request installation: GitHub", exact: true });
    await submit.click();
    await expect(page.getByRole("alert")).toContainText("Request result uncertain");
    await expect(submit).toBeDisabled();
    expect(reads).toBeGreaterThanOrEqual(2);
    await page.getByRole("button", { name: "Refresh installation requests", exact: true }).click();
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect.poll(() => keys.length).toBe(2);
    await page.reload();
    await submit.click();
    await expect.poll(() => keys.length).toBe(3);
    expect(new Set(keys).size).toBe(1);
});

for (const decision of ["approve", "reject"] as const) {
    test(`admin ${decision} reviews exact staged revision and distinguishes install failure`, async ({ page }) => {
        let saved = {
            ...requestReceipt,
            decision: "pending",
            installation_status: "not_started",
            installation_error: null as string | null,
            reason: null as string | null,
        };
        let writes = 0;
        let boardWrites = 0;
        await page.route("**/board/**", (route) => {
            if (route.request().method() === "GET") return route.continue();
            boardWrites++;
            return route.abort();
        });
        await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official, installed: [] } }));
        await page.route("**/apps/installation-requests?*", (route) => route.fulfill({ json: { items: [saved], next_cursor: null } }));
        await page.route("**/apps/installation-requests/ABCDEFGHIJK", (route) => route.fulfill({ json: { request: saved } }));
        await page.route("**/settings/apps/installation-requests/ABCDEFGHIJK/review", async (route) => {
            expect(route.request().postDataJSON()).toEqual({ decision, expected_revision: revision, reason: "Reviewed exact artifact" });
            saved = {
                ...saved,
                decision: decision === "approve" ? "approved" : "rejected",
                installation_status: decision === "approve" ? "failed" : "not_started",
                installation_error: decision === "approve" ? "app_revision_conflict" : null,
                reason: "Reviewed exact artifact",
                revision: "e".repeat(64),
            };
            writes++;
            await route.fulfill({ json: { request: saved } });
        });
        await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
        await page.getByRole("button", { name: "Review installation request: GitHub", exact: true }).click();
        const detail = page.getByRole("article", { name: "Staged package identity" });
        await expect(detail).toContainText("d".repeat(64));
        await expect(detail).toContainText("v1.2.3");
        await page.getByLabel("Review reason", { exact: true }).fill("Reviewed exact artifact");
        await page.getByRole("button", { name: decision === "approve" ? "Approve request" : "Reject request", exact: true }).click();
        await expect(detail).toContainText(decision === "approve" ? "Approved · Installation failed" : "Rejected · Installation not started");
        await expect(page.getByRole("button", { name: "Approve request", exact: true })).toHaveCount(0);
        expect(writes).toBe(1);
        expect(boardWrites).toBe(0);
    });
}

test("requester identity switch clears staged detail and request cache view", async ({ page }) => {
    let reads = 0;
    await page.route("**/apps/installation-requests?*", (route) => {
        reads++;
        return route.fulfill({ json: { items: reads === 1 ? [requestReceipt] : [], next_cursor: null } });
    });
    await page.route("**/apps/installation-requests/ABCDEFGHIJK", (route) => route.fulfill({ json: { request: requestReceipt } }));
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html?owner&switch_user");
    await page.getByRole("button", { name: "Review installation request: GitHub", exact: true }).click();
    await expect(page.getByRole("article", { name: "Staged package identity" })).toBeVisible();
    await page.getByRole("button", { name: "Switch fixture user", exact: true }).click();
    await expect(page.getByRole("article", { name: "Staged package identity" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Review installation request: GitHub", exact: true })).toHaveCount(0);
    await expect.poll(() => reads).toBe(2);
});

test("requester can page through rejected and installed saved outcomes", async ({ page }) => {
    await page.route("**/apps/installation-requests?*", (route) => {
        const after = new URL(route.request().url()).searchParams.get("after");
        return route.fulfill({
            json: after
                ? {
                      items: [{ ...requestReceipt, uid: "NEXTREQ0001", decision: "approved", installation_status: "installed" }],
                      next_cursor: null,
                  }
                : { items: [{ ...requestReceipt, decision: "rejected", reason: "Policy review" }], next_cursor: requestReceipt.uid },
        });
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html?owner");
    await expect(page.getByText("Rejected · Installation not started", { exact: true })).toBeVisible();
    await expect(page.getByText("Policy review", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "More requests", exact: true }).click();
    await expect(page.getByText("Approved · Installed", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "More requests", exact: true })).toHaveCount(0);
});

test("ambiguous admin review reads back the saved decision before another action", async ({ page }) => {
    let saved = { ...requestReceipt };
    let detailReads = 0;
    let writes = 0;
    await page.route("**/settings/apps/packages", (route) => route.fulfill({ json: { official, installed: [] } }));
    await page.route("**/apps/installation-requests?*", (route) => route.fulfill({ json: { items: [saved], next_cursor: null } }));
    await page.route("**/apps/installation-requests/ABCDEFGHIJK", (route) => {
        detailReads++;
        return route.fulfill({ json: { request: saved } });
    });
    await page.route("**/settings/apps/installation-requests/ABCDEFGHIJK/review", async (route) => {
        saved = { ...saved, decision: "approved", installation_status: "installed", revision: "f".repeat(64) };
        writes++;
        await route.abort("failed");
    });
    await page.goto("/src/pages/SettingsPage/AppPackages.fixture.html");
    await page.getByRole("button", { name: "Review installation request: GitHub", exact: true }).click();
    await page.getByRole("button", { name: "Approve request", exact: true }).click();
    await expect(page.getByRole("article", { name: "Staged package identity" })).toContainText("Approved · Installed");
    await expect(page.getByRole("button", { name: "Approve request", exact: true })).toHaveCount(0);
    expect(detailReads).toBe(2);
    expect(writes).toBe(1);
});
