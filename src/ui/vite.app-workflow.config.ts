import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

const entries = [
    "src/pages/BoardPage/components/settings/AppWorkflow.fixture.tsx",
    "src/pages/BoardPage/components/settings/DevtronSettings.fixture.tsx",
];

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        cacheDir: "node_modules/.vite-app-workflow",
        optimizeDeps: { entries },
        server: { warmup: { clientFiles: entries } },
        plugins: [fixtureReadiness(entries.map((entry) => `/${entry}`))],
    })
);
