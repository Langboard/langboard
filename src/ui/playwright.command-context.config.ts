import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/pages/DashboardPage/components",
    testMatch: "command-context.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4185", channel: "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4185 --strictPort",
        url: "http://127.0.0.1:4185/src/pages/DashboardPage/components/command-context.fixture.html",
        // The fixture uses the real attachment URL guard, so Vite needs valid
        // local endpoint ports even though its Axios adapter supplies responses.
        env: { API_PORT: "5381", SOCKET_PORT: "5690", UI_PORT: "4185" },
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
