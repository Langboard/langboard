import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: ".",
    testMatch: "imageAnchors.spec.ts",
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4182", channel: "chrome", headless: true },
    webServer: {
        cwd: process.cwd(),
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4182 --strictPort",
        url: "http://127.0.0.1:4182/src/components/ImagePreviewDialog/imageAnchors.fixture.html",
        timeout: 60000,
        reuseExistingServer: false,
    },
});
