import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
    testDir: "./src/components/Layout",
    testMatch: "cmdk-native-query.spec.ts",
    outputDir: join(tmpdir(), "langboard-cmdk-native-query-results"),
    retries: 0,
    workers: 1,
    timeout: 60_000,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4197", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4197 --strictPort",
        env: { API_PORT: "5381", SOCKET_PORT: "5392", LANGBOARD_FIXTURE_CACHE_DIR: join(tmpdir(), "langboard-cmdk-native-cache") },
        url: "http://127.0.0.1:4197/src/components/Layout/cmdk-native-query.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
