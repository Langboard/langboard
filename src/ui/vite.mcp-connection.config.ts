import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

const entry = "src/pages/BoardPage/components/mcp/BoardMcpConnection.fixture.tsx";

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        cacheDir: "node_modules/.vite-mcp-connection",
        optimizeDeps: { entries: [entry] },
        server: { warmup: { clientFiles: [entry] } },
        plugins: [fixtureReadiness(`/${entry}`)],
    })
);
