import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/BoardPage/components/board",
    testMatch: "BoardColumnChildCollapse.spec.ts",
    outputDir: "/tmp/langboard-child-collapse-playwright",
    reporter: "line",
    timeout: 60_000,
    use: { baseURL: "http://127.0.0.1:4207", channel: "chrome", headless: true, hasTouch: true, viewport: { width: 1000, height: 800 } },
    webServer: {
        command:
            "API_PORT=5381 LANGBOARD_FIXTURE_CACHE_DIR=/tmp/langboard-child-collapse-vite node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4207 --strictPort",
        url: "http://127.0.0.1:4207/src/pages/BoardPage/components/board/BoardColumnChildCollapse.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
