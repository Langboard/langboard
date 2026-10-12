import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/components/Editor", testMatch: "editor-ref.spec.ts", retries: 0, reporter: "line",
    use: { baseURL: "http://127.0.0.1:4241", channel: "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4241 --strictPort",
        url: "http://127.0.0.1:4241/src/components/Editor/editor-ref.fixture.html", reuseExistingServer: false, timeout: 60000,
    },
});
