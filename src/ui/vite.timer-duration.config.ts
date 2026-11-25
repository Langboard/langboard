import { readdirSync } from "node:fs";
import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

const entries = [
    "src/core/hooks/TimerDuration.fixture.tsx",
    "src/pages/DashboardPage/components/TrackingRow.fixture.tsx",
    "src/pages/DashboardPage/TrackingLoad.fixture.tsx",
];
const defaultLocale = "src/assets/locales/en-US";
const localeModules = readdirSync(new URL(`./${defaultLocale}/`, import.meta.url))
    .filter((filename) => filename.endsWith(".json"))
    .map((filename) => `/${defaultLocale}/${filename}?import`);

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        cacheDir: process.env.LANGBOARD_FIXTURE_CACHE_DIR || "node_modules/.vite-timer-duration",
        optimizeDeps: { entries },
        server: { warmup: { clientFiles: entries } },
        plugins: [fixtureReadiness([...entries.map((entry) => `/${entry}`), ...localeModules])],
    })
);
