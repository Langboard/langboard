import { defineConfig } from "@playwright/test";
export default defineConfig({
    projects: [
        { name: "native-runs-http", testDir: "./src/components/Layout", testMatch: "automation-runs.spec.ts" },
        { name: "existing-scope-config", testDir: "./src/controllers/api/board", testMatch: "board-automation.spec.ts" },
    ],
    retries: 0,
    workers: 1,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4187", channel: "chrome", headless: true },
    webServer: [
        { command: "node src/components/Layout/automation-runs.http-fixture.mjs", url: "http://127.0.0.1:5387/control", reuseExistingServer: false },
        {
            command: "API_PORT=5387 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4187 --strictPort",
            url: "http://127.0.0.1:4187/src/components/Layout/automation-runs.fixture.html",
            reuseExistingServer: false,
            timeout: 60_000,
        },
    ],
});
