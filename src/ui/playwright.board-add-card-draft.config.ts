import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/core/providers",
    testMatch: "BoardAddCardProvider.spec.ts",
    retries: 0,
    workers: 1,
    reporter: "line",
    outputDir: "test-results/board-add-card-draft",
    use: { baseURL: "http://127.0.0.1:4257", channel: "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4257 --strictPort",
        url: "http://127.0.0.1:4257/src/core/providers/BoardAddCardProvider.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
