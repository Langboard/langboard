import { Server } from "@hocuspocus/server";
import { WebSocketServer } from "ws";
import { URL } from "node:url";

// Local browser fixture only. No application account, network, or persisted documents.
const notifications = new WebSocketServer({ noServer: true });
const server = new Server({
    address: "127.0.0.1",
    port: 4184,
    quiet: true,
    onUpgrade: async ({ request, socket, head }) => {
        if (new URL(request.url, "http://127.0.0.1").pathname === "/editor-sync") return;
        // The ordinary notification socket is JSON, not the editor's binary sync protocol.
        notifications.handleUpgrade(request, socket, head, (connection) => {
            connection.on("error", () => {});
        });
        throw null;
    },
    onAuthenticate: async ({ token }) => {
        if (token !== "card-timeline-local-fixture") throw new Error("Invalid fixture token");
    },
});
await server.listen();
