import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/components/Layout",
    testMatch: "card-timeline.spec.ts",
    grep: /comment timeline|column history/,
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4183", channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true },
    webServer: [
        { command: "node scripts/card-timeline-sync-fixture.mjs", url: "http://127.0.0.1:4184", reuseExistingServer: false },
        {
            command:
                "API_PORT=5381 SOCKET_PORT=4184 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4183 --strictPort",
            url: "http://127.0.0.1:4183/src/components/Layout/card-timeline.fixture.html",
            reuseExistingServer: false,
            timeout: 60_000,
        },
    ],
});
