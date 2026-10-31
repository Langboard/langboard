import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../../..");
const evidence = path.resolve(ui, "../../.verification/relations-tree-completion");
export default defineConfig({
    testDir: ".",
    testMatch: "relations.spec.ts",
    workers: 1,
    retries: 0,
    timeout: 30_000,
    reporter: [["line"], ["json", { outputFile: path.join(evidence, "browser-results.json") }]],
    outputDir: path.join(evidence, "browser-artifacts"),
    use: {
        baseURL: "http://127.0.0.1:4193",
        channel: "chrome",
        headless: true,
        viewport: { width: 1100, height: 900 },
        screenshot: "only-on-failure",
    },
    webServer: {
        cwd: ui,
        command:
            `API_PORT=5381 LANGBOARD_FIXTURE_CACHE_DIR=${evidence}/vite-cache ` +
            "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4193 --strictPort",
        url: "http://127.0.0.1:4193/src/pages/BoardPage/components/board/relations-tree-tests/relations.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
