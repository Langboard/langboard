import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src",
    testMatch: ["CardAppInteractions.spec.ts", "BoardCardCheckitemTimer.spec.ts", "card-action-surfaces.spec.ts"],
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4256", channel: "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4256 --strictPort",
        url: "http://127.0.0.1:4256",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
