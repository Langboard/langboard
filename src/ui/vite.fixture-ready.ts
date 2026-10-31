import type { Plugin } from "vite";

// Keep cold module preparation outside the browser interaction timeout.
export default function fixtureReadiness(entry: string): Plugin {
    return {
        name: "native-fixture-readiness",
        configureServer(server) {
            let ready: Promise<void> | undefined;
            server.middlewares.use((request, response, next) => {
                if (request.url?.split("?")[0] !== "/__fixture_ready__") return next();
                ready ??= (async () => {
                    const client = server.environments.client;
                    if (!(await client.transformRequest(entry))) throw new Error(`Missing fixture entry: ${entry}`);
                    await client.waitForRequestsIdle();
                    const optimizer = client.depsOptimizer;
                    await optimizer?.scanProcessing;
                    await Promise.all(Object.values(optimizer?.metadata.discovered ?? {}).map((dependency) => dependency.processing));
                })();
                ready.then(() => {
                    response.statusCode = 204;
                    response.end();
                }, next);
            });
        },
    };
}
