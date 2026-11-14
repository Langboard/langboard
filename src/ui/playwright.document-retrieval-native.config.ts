import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/SettingsPage/components/internalBots",
    testMatch: "documentRetrievalNative.spec.ts",
    retries: 0,
    workers: 1,
    reporter: "line",
    outputDir: "../../.verification/card-work-wave-20261025/retrieval-settings/browser",
    use: { baseURL: "http://127.0.0.1:4218", channel: "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4218 --strictPort",
        url: "http://127.0.0.1:4218",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
