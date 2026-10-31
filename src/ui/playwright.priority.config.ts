import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src",
    testMatch: ["**/SteppedChoiceSlider.spec.ts", "**/CardPriorityField.spec.ts", "**/BoardPriorityFilter.spec.ts"],
    workers: 1,
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4197", channel: "chromium", headless: true },
    webServer: {
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.priority.config.ts --host 127.0.0.1 --port 4197 --strictPort",
        url: "http://127.0.0.1:4197/__fixture_ready__",
        reuseExistingServer: false,
        timeout: 60000,
        stdout: "pipe",
        stderr: "pipe",
    },
});
