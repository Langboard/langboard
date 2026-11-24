import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/components/Layout",
    testMatch: "card-context.spec.ts",
    retries: 0,
    reporter: "line",
    workers: 1,
    use: { baseURL: "http://127.0.0.1:4183", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: [
        { command: "node scripts/collaborative-fixture-server.mjs", port: 4243, reuseExistingServer: false, timeout: 30000 },
        {
            command: "API_PORT=5381 node_modules/.bin/vite --config vite.collaborative-fixture.config.ts --host 127.0.0.1 --port 4183 --strictPort",
            url: "http://127.0.0.1:4183/src/components/Layout/workbench-shell.fixture.html",
            reuseExistingServer: false,
            timeout: 60000,
        },
    ],
});
