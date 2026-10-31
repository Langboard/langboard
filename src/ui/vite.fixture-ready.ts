import type { Plugin } from "vite";

// Keep cold module preparation outside the browser interaction timeout.
export default function fixtureReadiness(entry: string): Plugin {
    return {
        name: "native-fixture-readiness",
        configureServer(server) {
            const client = server.environments.client;
            let failed = false;
            const reportError = client.logger.error.bind(client.logger);
            client.logger.error = (...args) => {
                failed = true;
                reportError(...args);
            };
            const prepare = async (phase: string, operation: () => unknown) => {
                client.logger.info(`[fixture-ready] ${entry} ${phase}`, { timestamp: true });
                await operation();
                if (failed) throw new Error(`Fixture preparation failed: ${entry}. See Vite error output.`);
            };
            let ready: Promise<void> | undefined;
            server.middlewares.use((request, response, next) => {
                if (request.url?.split("?")[0] !== "/__fixture_ready__") return next();
                ready ??= (async () => {
                    await prepare("entry", async () => {
                        if (!(await client.transformRequest(entry))) throw new Error(`Missing fixture entry: ${entry}`);
                    });
                    await prepare("imports", () => client.waitForRequestsIdle());
                    const optimizer = client.depsOptimizer;
                    await prepare("dependency-scan", () => optimizer?.scanProcessing);
                    await prepare("dependency-build", () =>
                        Promise.all(Object.values(optimizer?.metadata.discovered ?? {}).map((dependency) => dependency.processing))
                    );
                    client.logger.info(`[fixture-ready] ${entry} ready`, { timestamp: true });
                })();
                ready.then(() => {
                    response.statusCode = 204;
                    response.end();
                }, next);
            });
        },
    };
}
