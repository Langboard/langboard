import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./src/components/Editor",
    testMatch: "McpWorkPlan.spec.ts",
    retries: 0,
    workers: 1,
    reporter: "line",
    outputDir: "test-results/mcp-work-plan",
    use: { channel: "chrome", headless: true },
});
