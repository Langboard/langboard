import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src",
    testMatch: [
        "**/board/crossBoard/CrossBoardDrop.spec.ts",
        "**/card/mirror/*.spec.ts",
        "**/cloud/BoardCloud.spec.ts",
        "**/ImagePreviewDialog/imageAnchors.spec.ts",
        "**/ImagePreviewDialog/imageContinuity.spec.ts",
        "**/SettingsPage/WorkflowStages.spec.ts",
        "**/DashboardPage/components/ProjectCollection.spec.ts",
        "**/DashboardPage/components/ProjectCollectionTree.spec.ts",
    ],
    workers: 2,
    retries: 0,
    reporter: "line",
    timeout: 60_000,
    use: {
        baseURL: "http://127.0.0.1:4197",
        channel: "chromium",
        headless: true,
        viewport: { width: 1440, height: 1000 },
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
    },
    webServer: {
        command:
            "API_PORT=5381 SOCKET_PORT=5382 node_modules/.bin/vite --config vite.board-resources.config.ts --host 127.0.0.1 --port 4197 --strictPort",
        url: "http://127.0.0.1:4197/__fixture_ready__",
        reuseExistingServer: false,
        timeout: 60_000,
        stdout: "pipe",
        stderr: "pipe",
    },
});
