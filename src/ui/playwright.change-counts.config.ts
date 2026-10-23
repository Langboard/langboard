import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
    testDir: "./src",
    testMatch: ["**/change-counts.spec.ts", "**/changes-native-query.spec.ts", "**/useMarkCardSeen.spec.ts", "**/useGetUnreadCardSummaries.spec.ts", "**/dashboard-unread.spec.ts"],
    outputDir: "../../.verification/card-change-count-ui/test-results",
    retries: 0,
    workers: 1,
    timeout: 60_000,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4196", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4196 --strictPort",
        env: { API_PORT: "5381", SOCKET_PORT: "5392", LANGBOARD_FIXTURE_CACHE_DIR: join(tmpdir(), "langboard-change-count-cache") },
        url: "http://127.0.0.1:4196/src/components/Layout/change-counts.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
