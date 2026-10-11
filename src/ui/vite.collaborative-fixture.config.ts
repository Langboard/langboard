import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import { fileURLToPath } from "node:url";
export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        resolve: {
            alias: [
                {
                    find: "@/core/providers/SocketProvider",
                    replacement: fileURLToPath(new URL("./src/components/Editor/collaborative-socket.fixture.ts", import.meta.url)),
                },
            ],
        },
    })
);
