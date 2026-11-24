import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src",
    testMatch: ["commentAnchor.spec.ts", "mobile-mention.spec.ts"],
    retries: 0,
    reporter: "line",
    use: { baseURL: "http://127.0.0.1:4178", channel: "chrome", headless: true },
    webServer: {
        command: "node_modules/.bin/vite --host 127.0.0.1 --port 4178 --strictPort",
        url: "http://127.0.0.1:4178/src/pages/BoardPage/components/card/comment/commentAnchor.fixture.html",
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
