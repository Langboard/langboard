import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/components/Editor/plugins/markdown-source",
    testMatch: "markdown-source.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4186", channel: "chrome", headless: true },
    webServer: {
        command: "yarn vite --host 127.0.0.1 --port 4186 --strictPort",
        url: "http://127.0.0.1:4186/src/components/Editor/plugins/markdown-source/markdown-source.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
