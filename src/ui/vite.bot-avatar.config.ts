import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        optimizeDeps: { entries: ["src/pages/SettingsPage/components/bots/bot-avatar.fixture.tsx"] },
        plugins: [fixtureReadiness("/src/pages/SettingsPage/components/bots/bot-avatar.fixture.tsx")],
    })
);
