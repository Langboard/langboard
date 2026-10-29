import process from "node:process";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { setTimeout, clearTimeout } from "node:timers";
import net from "node:net";

// Both CI and manual IAB use this exact runner. Prerequisite: the supported
// Elixir/OTP runtime, locked Phoenix deps, and MIX_ENV=test compilation.
export async function runCollaborativeOwner() {
    const probe = net.createServer();
    await new Promise((resolve, reject) => {
        probe.once("error", reject);
        probe.listen(4244, "127.0.0.1", resolve);
    });
    await new Promise((resolve) => probe.close(resolve));
    const directory = await mkdtemp(join(tmpdir(), "langboard-collaborative-owner-"));
    const children = new Set();
    let stopping = false;
    let cleanupPromise;
    const cleanup = () =>
        (cleanupPromise ??= (async () => {
            stopping = true;
            await Promise.all(
                [...children].map(
                    (child) =>
                        new Promise((resolve) => {
                            if (!child.pid || child.exitCode !== null || child.signalCode !== null) return resolve();
                            const timer = setTimeout(() => child.kill("SIGKILL"), 5_000);
                            child.once("exit", () => {
                                clearTimeout(timer);
                                resolve();
                            });
                            child.kill("SIGTERM");
                        })
                )
            );
            await rm(directory, { recursive: true, force: true });
            process.stdout.write(`Removed owned Phoenix fixture storage: ${directory}\n`);
        })());
    const onSignal = () => {
        void cleanup();
    };
    process.once("SIGTERM", onSignal);
    process.once("SIGINT", onSignal);
    let failure;
    const start = (command, args, options) => {
        const child = spawn(command, args, { stdio: "inherit", ...options });
        children.add(child);
        child.once("error", (error) => {
            failure = error;
            void cleanup();
        });
        child.once("exit", (code, signal) => {
            if (!stopping) {
                failure = new Error(`Owned fixture process ${child.pid} exited (${code ?? signal}).`);
                void cleanup();
            }
        });
        return child;
    };
    try {
        const code = `
if Mix.env() != :test, do: raise("The collaborative fixture requires MIX_ENV=test.")
Application.put_env(:langboard_socket, :editor_sync_enabled, true)
Application.put_env(:langboard_socket, :editor_sync_directory, System.fetch_env!("LANGBOARD_OWNER_FIXTURE_DIRECTORY"))
Application.put_env(:langboard_socket, :authorization_client, LangboardSocket.TestAuthClient)
Application.put_env(:langboard_socket, :kafka, enabled: false)
endpoint = Application.fetch_env!(:langboard_socket, LangboardSocketWeb.Endpoint)
Application.put_env(:langboard_socket, LangboardSocketWeb.Endpoint, Keyword.merge(endpoint, server: true, http: [ip: {127, 0, 0, 1}, port: 4244]))
{:ok, _apps} = Application.ensure_all_started(:langboard_socket)
Process.sleep(:infinity)
`;
        const owner = start("mix", ["run", "--no-start", "--eval", code], {
            cwd: fileURLToPath(new URL("../../socket/", import.meta.url)),
            env: { ...process.env, MIX_ENV: "test", LANGBOARD_OWNER_FIXTURE_DIRECTORY: directory },
        });
        process.stdout.write(
            `Owned native Phoenix fixture PID ${owner.pid}; storage ${directory}; TestAuthClient identity, not product ACL proof.\n`
        );
        const deadline = Date.now() + 25_000;
        let ready = false;
        while (!stopping && Date.now() < deadline) {
            try {
                const response = await globalThis.fetch("http://127.0.0.1:4244/health/ready", { signal: globalThis.AbortSignal.timeout(1_000) });
                ready = response.status === 204 && response.headers.get("x-langboard-socket-runtime") === "phoenix";
                if (ready) break;
            } catch {
                /* Startup may still be binding the owned endpoint. */
            }
            await new Promise((resolve) => setTimeout(resolve, 200));
        }
        if (stopping && !failure) return;
        if (!ready)
            throw failure ?? new Error("The owned Phoenix fixture did not become ready. Prepare locked MIX_ENV=test dependencies and compile first.");
        const proxy = start(process.execPath, [fileURLToPath(new URL("./collaborative-fixture-server.mjs", import.meta.url))], {
            env: { ...process.env, COLLABORATIVE_PHOENIX_ORIGIN: "http://127.0.0.1:4244" },
        });
        await new Promise((resolve) => proxy.once("exit", resolve));
        if (failure) throw failure;
    } finally {
        await cleanup();
        process.off("SIGTERM", onSignal);
        process.off("SIGINT", onSignal);
    }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await runCollaborativeOwner();
