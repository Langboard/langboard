import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const uiRoot = fileURLToPath(new URL("../../../../../", import.meta.url));

export default defineConfig({
    testDir: ".",
    testMatch: "BoardProxy.spec.ts",
    outputDir: resolve(uiRoot, "../../.verification/move-location-20261022/tracked-config-results"),
    fullyParallel: false,
    retries: 0,
    reporter: "line",
    use: {
        baseURL: "http://127.0.0.1:4298",
        channel: "chrome",
        headless: true,
        viewport: { width: 1280, height: 900 },
    },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4298 --strictPort",
        cwd: uiRoot,
        env: { API_PORT: "5381", SOCKET_PORT: "5382", LANGBOARD_FIXTURE_CACHE_DIR: "node_modules/.vite/move-location" },
        url: "http://127.0.0.1:4298/src/controllers/api/card/CardLocationResolver/BoardProxy.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
