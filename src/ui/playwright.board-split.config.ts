import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/BoardPage",
    testMatch: "BoardSplit.spec.ts",
    retries: 0,
    reporter: "line",
    timeout: 60_000,
    use: { baseURL: "http://127.0.0.1:4196", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: [
            "API_PORT=5381 LANGBOARD_FIXTURE_CACHE_DIR=/tmp/langboard-board-split-vite-20261022",
            "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4196 --strictPort",
        ].join(" "),
        url: "http://127.0.0.1:4196/src/pages/BoardPage/BoardSplit.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
