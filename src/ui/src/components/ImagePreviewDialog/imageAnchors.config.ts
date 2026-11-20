import { defineConfig } from "@playwright/test";
const port = Number(process.env.IMAGE_ANCHOR_PORT || 4182);
export default defineConfig({
    testDir: ".",
    testMatch: ["imageAnchors.spec.ts", "imageContinuity.spec.ts"],
    retries: 0,
    reporter: "line",
    use: { baseURL: `http://127.0.0.1:${port}`, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true },
    webServer: {
        cwd: process.cwd(),
        command: `node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port ${port} --strictPort`,
        url: `http://127.0.0.1:${port}/src/components/ImagePreviewDialog/imageAnchors.fixture.html`,
        timeout: 60000,
        reuseExistingServer: false,
    },
});
