import { defineConfig } from "@playwright/test";
const verificationDir = process.env.LANGBOARD_SCHEDULE_VERIFICATION_DIR || "test-results/schedule-workload";
export default defineConfig({
    testDir: "./src/components/Layout",
    testMatch: "schedule-workload.spec.ts",
    workers: 1,
    retries: 0,
    timeout: 90000,
    outputDir: `${verificationDir}/browser-results`,
    reporter: "line",
    use: {
        baseURL: "http://127.0.0.1:4199",
        channel: "chrome",
        headless: true,
        viewport: { width: 1440, height: 900 },
        timezoneId: "America/New_York",
    },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4199 --strictPort",
        env: { API_PORT: "5381", SOCKET_PORT: "5690", LANGBOARD_FIXTURE_CACHE_DIR: `${verificationDir}/vite-cache` },
        url: "http://127.0.0.1:4199/src/pages/BoardPage/BoardSplit.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
