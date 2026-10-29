import assert from "node:assert/strict";
import { test } from "node:test";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import { WebSocketServer, WebSocket } from "ws";
import { HocuspocusProvider } from "@hocuspocus/provider";
import * as Y from "yjs";
import * as decoding from "lib0/decoding";
import { createEditorAuthentication } from "./editorAuthentication.ts";
// Browser WebSocket reports errors as events; ws additionally treats an
// unhandled Node EventEmitter error as fatal during a cancelled handshake.
class BrowserWebSocket extends WebSocket {
    constructor(url) {
        super(url);
        this.on("error", () => { });
    }
}
async function exercise(code, guarded) {
    const server = new WebSocketServer({ port: 0, host: "127.0.0.1" });
    await once(server, "listening");
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    let connections = 0;
    let token = "old-credential";
    const tokens = [];
    server.on("connection", (socket) => {
        connections++;
        socket.on("message", (data) => {
            const decoder = decoding.createDecoder(new Uint8Array(data));
            decoding.readVarString(decoder);
            if (decoding.readVarUint(decoder) !== 2 || decoding.readVarUint(decoder) !== 0)
                return;
            tokens.push(decoding.readVarString(decoder));
            setTimeout(() => socket.close(code), 20);
        });
    });
    const doc = new Y.Doc();
    doc.getText("title").insert(0, "unsaved local draft");
    let provider;
    const auth = createEditorAuthentication(() => provider, () => `ws://127.0.0.1/editor-sync?authorization=${token}`);
    provider = new HocuspocusProvider({
        name: "test-document", url: `ws://127.0.0.1:${address.port}`,
        document: doc, WebSocketPolyfill: BrowserWebSocket,
        ...auth,
        ...(guarded ? {} : { onDisconnect: () => { } }),
    });
    // Exercise the installed provider's real TCP close/retry behavior.
    try {
        const deadline = Date.now() + 3000;
        while (tokens.length === 0 && Date.now() < deadline)
            await delay(10);
        assert.equal(tokens[0], "old-credential");
        await delay(1250);
        if (guarded && (code === 4401 || code === 4403)) {
            assert.equal(connections, 1);
            assert.equal(provider.configuration.websocketProvider.shouldConnect, false);
            assert.equal(doc.getText("title").toString(), "unsaved local draft");
            token = "refreshed-credential";
            await provider.connect();
            const retryDeadline = Date.now() + 1000;
            while (tokens.length < 2 && Date.now() < retryDeadline)
                await delay(10);
            assert.equal(tokens[1], "refreshed-credential");
        }
        else {
            assert.ok(connections >= 2, `expected reconnect for ${code}; observed ${connections}`);
        }
    }
    finally {
        // The native provider can already have queued a reconnect timer. Keep
        // that timer from reopening a disposed test transport after destroy.
        provider.configuration.websocketProvider.connect = async () => { };
        provider.destroy();
        doc.destroy();
        for (const socket of server.clients)
            socket.terminate();
        await new Promise((resolve) => server.close(() => resolve()));
    }
}
test("native unguarded provider repeats authentication refusals", () => exercise(4401, false));
for (const code of [4401, 4403]) {
    test(`server ${code} stops retries, retains drafts, and allows retry with fresh credentials`, () => exercise(code, true));
}
test("transient server errors retain native automatic recovery", () => exercise(1011, true));
