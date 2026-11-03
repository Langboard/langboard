import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/SettingsPage/components/users",
    testMatch: "group-directory.spec.ts",
    outputDir: "/private/tmp/langboard-group-directory-results",
    retries: 0,
    workers: 1,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4187", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command:
            "LANGBOARD_FIXTURE_CACHE_DIR=/private/tmp/langboard-group-directory-vite API_PORT=5381 SOCKET_PORT=5382 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4187 --strictPort",
        url: "http://127.0.0.1:4187/src/pages/SettingsPage/components/users/group-directory.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
