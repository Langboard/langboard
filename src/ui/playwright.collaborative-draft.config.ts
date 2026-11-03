import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/components/Editor",
    testMatch: ["collaborative-draft.spec.ts", "collaborative-card-draft.spec.ts"],
    workers: 1,
    retries: 0,
    reporter: "line",
    outputDir: process.env.LANGBOARD_FIXTURE_OUTPUT_DIR || "test-results/collaborative-draft",
    use: { baseURL: "http://127.0.0.1:4242", channel: "chromium", headless: true },
    webServer: [
        { command: "node scripts/run-collaborative-owner.mjs", port: 4243, reuseExistingServer: false, timeout: 30000 },
        {
            command: "node_modules/.bin/vite --config vite.collaborative-fixture.config.ts --host 127.0.0.1 --port 4242 --strictPort",
            url: "http://127.0.0.1:4242/src/components/Editor/collaborative-draft.fixture.html",
            reuseExistingServer: false,
            timeout: 60000,
        },
    ],
});
