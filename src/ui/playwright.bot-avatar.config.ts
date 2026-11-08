import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/pages/SettingsPage/components/bots",
    testMatch: "bot-avatar.spec.ts",
    outputDir: "test-results/bot-avatar",
    workers: 1,
    retries: 0,
    reporter: "line",
    use: {
        baseURL: "http://127.0.0.1:4185",
        channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
        headless: true,
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
    },
    webServer: {
        command: "API_PORT=5381 SOCKET_PORT=5690 node_modules/.bin/vite --config vite.bot-avatar.config.ts --host 127.0.0.1 --port 4185 --strictPort",
        url: "http://127.0.0.1:4185/__fixture_ready__",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
