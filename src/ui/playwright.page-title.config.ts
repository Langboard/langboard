import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: "./src/core/providers",
    testMatch: "PageTitleLocale.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4291", channel: "chrome", headless: true },
    webServer: {
        command: "PROJECT_NAME=Langboard node_modules/.bin/vite --config vite.page-title.config.ts --host 127.0.0.1 --port 4291 --strictPort",
        url: "http://127.0.0.1:4291/src/core/providers/PageTitleLocale.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
