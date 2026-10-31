import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";
export default defineConfig({
    testDir: "./src/pages/BoardPage/components/card/decision",
    testMatch: "CardDecision.spec.ts",
    retries: 0,
    reporter: "line",
    workers: 1,
    outputDir: join(tmpdir(), "card-decision-browser-results"),
    use: { baseURL: "http://127.0.0.1:4271", channel: process.env.CI ? undefined : "chrome", headless: true, viewport: { width: 320, height: 800 } },
    webServer: {
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4271 --strictPort",
        url: "http://127.0.0.1:4271",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
