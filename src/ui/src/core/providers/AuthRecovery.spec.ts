import { expect, test, type Page } from "@playwright/test";
const fixture = "/src/core/providers/AuthRecovery.fixture.html";
const identity = (uid: string) => ({
    user: {
        uid,
        type: "user",
        firstname: "Synthetic",
        lastname: "User",
        email: "user@example.invalid",
        username: uid,
        user_groups: [],
        api_key_role_actions: [],
        setting_role_actions: [],
        mcp_role_actions: [],
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
    },
    bots: [],
});
const deferred = () => {
    let resolve!: () => void;
    const promise = new Promise<void>((done) => {
        resolve = done;
    });
    return { promise, resolve };
};
const expectSignedOut = async (page: Page) => {
    await expect(page).toHaveURL(/\/auth\/signin$/);
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    await expect(page.locator("[role=status][aria-busy=true]")).toHaveCount(0);
    await expect(page.getByLabel("Auth state")).toHaveText(JSON.stringify({ state: "loaded", user: null, pathname: "/auth/signin" }));
};
const bootstrap = async (page: Page) => {
    await page.route("**/auth/me", (route) =>
        route.fulfill({ status: 200, json: identity(route.request().headers().authorization === "Bearer synthetic-session-b" ? "user-b" : "user-a") })
    );
    await page.route("**/auth/signout", (route) => route.fulfill({ status: 200, json: {} }));
};
test.afterEach(async ({ page }, testInfo) => {
    const path = testInfo.outputPath("rendered-auth-state.png");
    await page.screenshot({ path });
    await testInfo.attach("rendered-auth-state", { path, contentType: "image/png" });
});
for (const status of [422, 401]) {
    test(`initial refresh ${status} leaves BrandLoading and renders sign-in with one refresh`, async ({ page }) => {
        const release = deferred();
        let refreshes = 0;
        await page.route("**/auth/refresh", async (route) => {
            refreshes++;
            await release.promise;
            await route.fulfill({ status, json: {} });
        });
        await page.goto(fixture);
        await expect(page.locator("[role=status][aria-busy=true]")).toBeVisible();
        await expect(page.getByRole("main")).toHaveCount(0);
        release.resolve();
        await expectSignedOut(page);
        expect(refreshes).toBe(1);
    });
    test(`protected request with refresh ${status} rejects and renders sign-in`, async ({ page }) => {
        await bootstrap(page);
        let refreshes = 0;
        let reads = 0;
        await page.route("**/auth/refresh", (route) => {
            refreshes++;
            return route.fulfill(refreshes === 1 ? { status: 200, json: { access_token: "synthetic-session-a" } } : { status, json: {} });
        });
        await page.route("**/__auth-recovery-protected", (route) => {
            reads++;
            return route.fulfill({ status: 422, json: {} });
        });
        await page.goto(fixture);
        await expect(page.getByLabel("Auth state")).toContainText(JSON.stringify({ user: "user-a" }).slice(1, -1));
        await page.getByRole("button", { name: "Protected request", exact: true }).click();
        await expectSignedOut(page);
        await expect(page.getByLabel("Request result")).toHaveText("rejected");
        expect({ refreshes, reads }).toEqual({ refreshes: 2, reads: 1 });
    });
}
test("signed-out client navigation from sign-in re-evaluates the protected route", async ({ page }) => {
    let refreshes = 0;
    await page.route("**/auth/refresh", (route) => {
        refreshes++;
        return route.fulfill({ status: 401, json: {} });
    });
    await page.goto(`${fixture}?initial=/auth/signin`);
    await expectSignedOut(page);
    await page.getByRole("button", { name: "Open protected page", exact: true }).click();
    await expectSignedOut(page);
    expect(refreshes).toBe(1);
});
for (const action of ["Logout", "Replace session"]) {
    for (const staleStatus of [200, 401, 422]) {
        test(`${action} isolates rendered identity from stale refresh ${staleStatus}`, async ({ page }) => {
            await bootstrap(page);
            const release = deferred();
            const pending = deferred();
            let refreshes = 0;
            let reads = 0;
            await page.route("**/auth/refresh", async (route) => {
                refreshes++;
                if (refreshes === 1) {
                    await route.fulfill({ status: 200, json: { access_token: "synthetic-session-a" } });
                    return;
                }
                pending.resolve();
                await release.promise;
                await route.fulfill({ status: staleStatus, json: staleStatus === 200 ? { access_token: "stale-session" } : {} });
            });
            await page.route("**/__auth-recovery-protected", (route) => {
                reads++;
                return route.fulfill({ status: 422, json: {} });
            });
            await page.goto(fixture);
            await expect(page.getByLabel("Auth state")).toContainText(JSON.stringify({ user: "user-a" }).slice(1, -1));
            await page.getByRole("button", { name: "Protected request", exact: true }).click();
            await pending.promise;
            await page.getByRole("button", { name: action, exact: true }).click();
            if (action === "Logout") await expectSignedOut(page);
            else await expect(page.getByLabel("Auth state")).toHaveText(JSON.stringify({ state: "loaded", user: "user-b", pathname: "/dashboard" }));
            release.resolve();
            await expect(page.getByLabel("Request result")).toHaveText("rejected");
            if (action === "Logout") await expectSignedOut(page);
            else {
                await expect(page.getByLabel("Auth state")).toHaveText(JSON.stringify({ state: "loaded", user: "user-b", pathname: "/dashboard" }));
                await expect(page.getByRole("heading", { name: "Protected page", exact: true })).toBeVisible();
                await expect(page.locator("[role=status][aria-busy=true]")).toHaveCount(0);
            }
            expect({ refreshes, reads }).toEqual({ refreshes: 2, reads: 1 });
        });
    }
}
