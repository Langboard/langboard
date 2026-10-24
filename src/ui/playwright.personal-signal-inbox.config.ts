import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src",
    testMatch: "PersonalSignalInbox.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4249", channel: process.env.CI ? undefined : "chrome", headless: true },
    webServer: {
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4249 --strictPort",
        url: "http://127.0.0.1:4249",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
