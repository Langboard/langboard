import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/components/AvatarGeneration",
    testMatch: "avatar-generation.spec.ts",
    workers: 1,
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4193", channel: "chrome", headless: true },
    webServer: {
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4193 --strictPort",
        url: "http://127.0.0.1:4193",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
