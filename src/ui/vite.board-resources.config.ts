import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

// Match native suite inputs instead of scanning unrelated app/panel entry points.
export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        optimizeDeps: {
            entries: [
                "src/components/ImagePreviewDialog/imageAnchors.fixture.html",
                "src/pages/BoardPage/BoardSplit.fixture.html",
                "src/pages/BoardPage/components/card/mirror/CardMirror.fixture.html",
                "src/pages/BoardPage/components/card/mirror/mirror.fixture.html",
                "src/pages/BoardPage/components/cloud/BoardCloud.fixture.html",
                "src/pages/DashboardPage/components/ProjectCollection.fixture.html",
                "src/pages/DashboardPage/components/ProjectCollectionTree.fixture.html",
                "src/pages/SettingsPage/CardFamilyMove.fixture.html",
                "src/pages/SettingsPage/WorkflowStages.fixture.html",
            ],
        },
        // Prepare the observed cold editor/provider graph before browser workers start.
        plugins: [fixtureReadiness("/src/components/ImagePreviewDialog/imageAnchors.fixture.tsx")],
    })
);
