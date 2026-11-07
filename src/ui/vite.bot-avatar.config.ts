import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

const entry = "src/pages/SettingsPage/components/bots/bot-avatar.fixture.tsx";

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        cacheDir: "node_modules/.vite-bot-avatar",
        optimizeDeps: { entries: [entry] },
        server: { warmup: { clientFiles: [entry] } },
        plugins: [fixtureReadiness(`/${entry}`)],
    })
);
