import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/core/providers",
    testMatch: "AuthRecovery.spec.ts",
    retries: 0,
    reporter: [["line"], ["json", { outputFile: "output/playwright/auth-recovery-results.json" }]],
    outputDir: "output/playwright/auth-recovery",
    use: { baseURL: "http://127.0.0.1:4218", channel: "chrome", headless: true, trace: "retain-on-failure" },
    webServer: {
        command: [
            "API_PORT=5381 LANGBOARD_FIXTURE_CACHE_DIR=node_modules/.vite-auth-recovery",
            "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4218 --strictPort",
        ].join(" "),
        url: "http://127.0.0.1:4218/src/core/providers/AuthRecovery.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
