import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
export default defineConfig({
    testDir: ".",
    testMatch: "BoardCloud.spec.ts",
    retries: 0,
    reporter: "line",
    outputDir: "/tmp/langboard-cloud-playwright",
    use: { baseURL: "http://127.0.0.1:4194", channel: "chrome", headless: true, viewport: { width: 1100, height: 1000 } },
    webServer: {
        cwd: fileURLToPath(new URL("../../../../../", import.meta.url)),
        command: [
            "TMPDIR=/tmp LANGBOARD_FIXTURE_CACHE_DIR=/tmp/langboard-cloud-vite API_PORT=5394",
            "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4194 --strictPort",
        ].join(" "),
        url: "http://127.0.0.1:4194/src/pages/BoardPage/components/cloud/BoardCloud.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
