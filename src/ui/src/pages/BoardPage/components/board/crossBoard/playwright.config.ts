import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: ".",
    testMatch: "CrossBoardDrop.spec.ts",
    retries: 0,
    workers: 1,
    reporter: "line",
    timeout: 60_000,
    outputDir: "/tmp/langboard-cross-board-results",
    use: { baseURL: "http://127.0.0.1:4197", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: [
            "API_PORT=5381 TMPDIR=/tmp LANGBOARD_FIXTURE_CACHE_DIR=.verification/cross-board-move-cache/node_modules/.vite",
            "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4197 --strictPort",
        ].join(" "),
        cwd: process.cwd(),
        url: "http://127.0.0.1:4197/src/pages/BoardPage/BoardSplit.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
