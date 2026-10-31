import { defineConfig } from "@playwright/test";
import { fullEditServer } from "./playwright.full-edit-server";

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
    webServer: [
        {
            command: "node_modules/.bin/vite --config vite.card-draft-close.config.ts --host 127.0.0.1 --port 4182 --strictPort",
            url: "http://127.0.0.1:4182/__fixture_ready__",
            reuseExistingServer: false,
            timeout: 60000,
        },
        fullEditServer,
    ],
});
