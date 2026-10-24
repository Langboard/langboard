import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
    testDir: "./src/components/Layout",
    testMatch: "changes-native-query.spec.ts",
    outputDir: "../../.verification/changes-native-query-performance/test-results",
    retries: 0,
    workers: 1,
    timeout: 60_000,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4195", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4195 --strictPort",
        env: { API_PORT: "5381", SOCKET_PORT: "5392", LANGBOARD_FIXTURE_CACHE_DIR: join(tmpdir(), "langboard-changes-native-cache") },
        url: "http://127.0.0.1:4195/src/components/Layout/changes-native-query.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
