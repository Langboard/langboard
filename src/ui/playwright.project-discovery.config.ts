import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/pages/DashboardPage/components",
    testMatch: ["project-discovery-ui.spec.ts", "workload.spec.ts"],
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4284", channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true },
    webServer: {
        command: "API_PORT=5381 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4284 --strictPort",
        url: "http://127.0.0.1:4284/src/pages/DashboardPage/components/project-discovery-ui.fixture.html",
        timeout: 60000,
    },
});
