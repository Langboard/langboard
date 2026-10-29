import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/pages/BoardPage/components/card",
    testMatch: ["cardDraftClose.spec.ts", "fullEditSave.spec.ts"],
    workers: 1,
    retries: 0,
    reporter: "line",
    use: {
        baseURL: "http://127.0.0.1:4182",
        channel: "chromium",
        headless: true,
        reducedMotion: "reduce",
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
    },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-draft-close.config.ts --host 127.0.0.1 --port 4182 --strictPort",
        url: "http://127.0.0.1:4182/src/pages/BoardPage/components/card/cardDraftClose.fixture.html",
        reuseExistingServer: false,
        timeout: 60000,
    },
});
