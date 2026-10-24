import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";
export default defineConfig({
    testDir: "./src/components/Editor",
    testMatch: "wiki-secret-selection.spec.ts",
    retries: 0,
    reporter: "line",
    workers: 1,
    outputDir: join(tmpdir(), "wiki-secret-selection-browser-results"),
    use: { baseURL: "http://127.0.0.1:4259", channel: process.env.CI ? undefined : "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4259 --strictPort",
        url: "http://127.0.0.1:4259",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
