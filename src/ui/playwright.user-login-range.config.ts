import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/SettingsPage/components/users",
    testMatch: ["user-login-range.spec.ts", "user-business-activity.spec.ts", "user-effective-permissions.spec.ts"],
    outputDir: "/private/tmp/langboard-user-login-range-results",
    retries: 0,
    workers: 1,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4199", channel: process.env.CI ? undefined : "chrome", headless: true, timezoneId: "Asia/Seoul", viewport: { width: 1440, height: 900 } },
    webServer: {
        // Keep optimized dependencies out of React's application-source transforms.
        command:
            "LANGBOARD_FIXTURE_CACHE_DIR=/private/tmp/node_modules/langboard-user-login-range-vite API_PORT=5381 SOCKET_PORT=5382 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4199 --strictPort",
        url: "http://127.0.0.1:4199/src/pages/SettingsPage/components/users/group-directory.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
