import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/components/VideoPlayer",
    testMatch: "video-player.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4187", channel: "chrome", headless: true },
    webServer: {
        command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4187 --strictPort",
        url: "http://127.0.0.1:4187/src/components/VideoPlayer/video-player.fixture.html",
        reuseExistingServer: false,
        env: { API_PORT: "5381" },
        timeout: 60_000,
    },
});
