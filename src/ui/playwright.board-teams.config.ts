import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/BoardPage/components/settings",
    testMatch: "BoardSettingsTeams.spec.ts",
    retries: 0,
    reporter: "line",
    outputDir: "/private/tmp/board-teams-browser-results",
    use: { baseURL: "http://127.0.0.1:4267", channel: "chrome", headless: true, viewport: { width: 320, height: 720 } },
    webServer: {
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4267 --strictPort",
        url: "http://127.0.0.1:4267",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
