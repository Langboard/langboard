import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: ".",
    testMatch: "mirror.spec.ts",
    outputDir: "/tmp/langboard-card-mirror-results",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4198", channel: "chrome", headless: true },
    webServer: {
        cwd: process.cwd(),
        command:
            "TMPDIR=/tmp LANGBOARD_FIXTURE_CACHE_DIR=/tmp/langboard-card-mirror-vite API_PORT=5381 " +
            "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4198 --strictPort",
        url: "http://127.0.0.1:4198",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
