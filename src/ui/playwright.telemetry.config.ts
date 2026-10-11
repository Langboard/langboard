import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/core/helpers",
    testMatch: "BrowserTelemetry.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4217", channel: "chrome", headless: true },
    webServer: {
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4217 --strictPort",
        url: "http://127.0.0.1:4217/src/core/helpers/BrowserTelemetry.fixture.html?disabled",
        reuseExistingServer: true,
        timeout: 60000,
    },
});
