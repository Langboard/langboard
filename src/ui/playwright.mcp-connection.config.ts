import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/pages/BoardPage/components/mcp",
    testMatch: "BoardMcpConnection.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4295", channel: "chrome", headless: true, viewport: { width: 320, height: 720 } },
    webServer: {
        command: "API_PORT=5381 SOCKET_PORT=5382 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4295 --strictPort",
        url: "http://127.0.0.1:4295/src/pages/BoardPage/components/mcp/BoardMcpConnection.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
