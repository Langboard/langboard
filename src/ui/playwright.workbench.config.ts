import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/components",
    testMatch: ["Layout/workbench-shell.spec.ts", "ResizableSidebar/state.spec.ts"],
    grepInvert: /responsive native comments/,
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4183", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: "API_PORT=5381 SOCKET_PORT=5382 node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4183 --strictPort",
        url: "http://127.0.0.1:4183/src/components/Layout/workbench-shell.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
