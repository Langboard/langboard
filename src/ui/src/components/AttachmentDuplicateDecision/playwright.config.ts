import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: ".",
    testMatch: "upload.spec.ts",
    retries: 0,
    timeout: 60000,
    reporter: "line",
    outputDir: "/tmp/langboard-duplicate-results",
    workers: 1,
    use: { baseURL: "http://127.0.0.1:4214", channel: "chrome", headless: true },
    webServer: {
        cwd: new URL("../../../", import.meta.url).pathname,
        command:
            "node_modules/.bin/vite --config src/components/AttachmentDuplicateDecision/vite.config.mjs --host 127.0.0.1 --port 4214 --strictPort",
        url: "http://127.0.0.1:4214",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
