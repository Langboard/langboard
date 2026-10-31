import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createLogger, createServer } from "vite";
import fixtureReadiness from "../vite.fixture-ready.ts";

for (const failure of [null, "warning", "entry", "dependency", "optimizer"]) {
    const rejected = failure !== null && failure !== "warning";
    test(`readiness ${rejected ? `rejects ${failure} failure` : `accepts ${failure ?? "successful"} preparation`}`, { timeout: 15000 }, async (t) => {
        const root = await mkdtemp(join(tmpdir(), "langboard-readiness-"));
        const errors = [];
        const phases = [];
        const logger = createLogger("silent");
        logger.error = (message, options) => errors.push({ message, error: options?.error });
        logger.info = (message) => {
            if (message.startsWith("[fixture-ready]")) phases.push(message.split(" ").at(-1));
        };
        const entry = failure === "entry" ? "export const value = ;" : "import { value } from './dependency.ts'; export { value };";
        const dependency = failure === "dependency" ? "export const value = ;" : "export const value = 1;";
        await writeFile(join(root, "entry.ts"), entry);
        await writeFile(join(root, "dependency.ts"), dependency);
        if (failure === "optimizer") {
            const packageRoot = join(root, "node_modules/fixture-broken-dependency");
            await mkdir(packageRoot, { recursive: true });
            await writeFile(
                join(packageRoot, "package.json"),
                JSON.stringify({ name: "fixture-broken-dependency", version: "1.0.0", type: "module", main: "index.js" })
            );
            await writeFile(join(packageRoot, "index.js"), "export const value = ;");
        }
        const server = await createServer({
            configFile: false,
            root,
            cacheDir: join(root, "node_modules/.vite"),
            customLogger: logger,
            optimizeDeps: {
                noDiscovery: true,
                entries: ["entry.ts"],
                include: failure === "optimizer" ? ["fixture-broken-dependency"] : [],
            },
            plugins: [
                fixtureReadiness("/entry.ts"),
                {
                    name: "native-warning-control",
                    configureServer(instance) {
                        if (failure === "warning") instance.environments.client.logger.warn("Fixture warning control");
                    },
                },
            ],
            server: { host: "127.0.0.1", port: 0, strictPort: true, watch: null },
        });
        try {
            if (failure === "optimizer") {
                await assert.rejects(server.listen(), /dependency optimization/);
                assert.equal(server.httpServer.listening, false);
                return;
            }
            await server.listen();
            const url = server.resolvedUrls.local[0] + "__fixture_ready__";
            const results = await Promise.all([globalThis.fetch(url, { signal: t.signal }), globalThis.fetch(url, { signal: t.signal })]);
            assert.deepEqual(
                results.map((response) => response.status),
                [rejected ? 500 : 204, rejected ? 500 : 204]
            );
            await Promise.all(results.map((response) => response.arrayBuffer()));
            if (rejected) {
                assert.ok(errors.some(({ message }) => message.includes("Unexpected")));
                assert.ok(errors.some(({ error }) => error instanceof Error));
            } else {
                assert.deepEqual(errors, []);
                assert.deepEqual(phases, ["entry", "imports", "dependency-scan", "dependency-build", "ready"]);
            }
        } finally {
            await server.close();
            await rm(root, { recursive: true, force: true });
        }
    });
}
