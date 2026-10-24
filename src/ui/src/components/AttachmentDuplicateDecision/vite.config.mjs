import { defineConfig, loadConfigFromFile, mergeConfig } from "vite";
import { fileURLToPath, URL } from "node:url";
import process from "node:process";
export default defineConfig(async (env) => {
    const base = await loadConfigFromFile(env, fileURLToPath(new URL("../../../vite.config.ts", import.meta.url)));
    if (!base) throw new Error("Native Vite configuration is required");
    return mergeConfig(base.config, {
        define: { "process.env.API_URL": JSON.stringify("http://127.0.0.1:4214") },
        optimizeDeps: { entries: ["src/components/AttachmentDuplicateDecision/upload.fixture.html"] },
        cacheDir: process.env.LANGBOARD_FIXTURE_CACHE_DIR ?? "/tmp/owned-cache",
    });
});
