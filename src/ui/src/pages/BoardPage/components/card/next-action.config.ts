import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: ".",
    testMatch: "NextAction.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4209", channel: "chromium", headless: true },
    webServer: {
        cwd: fileURLToPath(new URL("../../../../../", import.meta.url)),
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4209 --strictPort",
        url: "http://127.0.0.1:4209/src/pages/BoardPage/components/card/next-action.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
