import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        optimizeDeps: {
            entries: [
                "src/components/SteppedChoiceSlider.fixture.html",
                "src/pages/BoardPage/components/card/CardPriorityField.fixture.html",
                "src/pages/BoardPage/components/board/BoardFilter.fixture.html",
            ],
        },
        plugins: [fixtureReadiness("/src/pages/BoardPage/components/board/BoardFilter.fixture.tsx")],
    })
);
