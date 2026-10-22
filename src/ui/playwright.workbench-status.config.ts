import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
    testDir: "./src/components/Layout",
    testMatch: ["workbench-status.spec.ts", "workbench-shell.spec.ts"],
    grep: /all footer fields|footer observes actual transport/,
    retries: 0,
    timeout: 90_000,
    workers: 1,
    reporter: "line",
    outputDir: `../../.verification/workbench-narrow-status/${process.env.STATUS_EVIDENCE_PHASE || "green"}-results`,
    use: { baseURL: "http://127.0.0.1:4194", channel: "chrome", headless: true, viewport: { width: 1440, height: 900 } },
    webServer: {
        command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4194 --strictPort",
        env: { API_PORT: "5381", SOCKET_PORT: "5382", LANGBOARD_FIXTURE_CACHE_DIR: join(tmpdir(), "langboard-workbench-status-cache") },
        url: "http://127.0.0.1:4194/src/pages/BoardPage/BoardSplit.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
