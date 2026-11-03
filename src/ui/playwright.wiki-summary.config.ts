import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/pages/BoardPage/components/board",
    testMatch: ["wiki-summary-tests/WikiSummary.spec.ts", "wiki-load.spec.ts"],
    retries: 0,
    workers: 1,
    reporter: "line",
    timeout: 30_000,
    use: { baseURL: "http://127.0.0.1:4191", channel: "chrome", headless: true, viewport: { width: 1100, height: 900 } },
    webServer: {
        command: "API_PORT=5391 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4191 --strictPort",
        url: "http://127.0.0.1:4191/src/pages/BoardPage/components/board/wiki-summary-tests/WikiSummary.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
