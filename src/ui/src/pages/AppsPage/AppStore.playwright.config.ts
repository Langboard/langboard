import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: ".",
    testMatch: "AppStore.spec.ts",
    retries: 0,
    reporter: "line",
    outputDir: fileURLToPath(new URL("../../../test-results/app-catalog", import.meta.url)),
    use: { baseURL: "http://127.0.0.1:4293", channel: "chrome", headless: true },
    webServer: {
        cwd: fileURLToPath(new URL("../../../", import.meta.url)),
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4293 --strictPort",
        url: "http://127.0.0.1:4293/src/pages/AppsPage/AppStore.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
