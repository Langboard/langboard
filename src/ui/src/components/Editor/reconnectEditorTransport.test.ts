import assert from "node:assert/strict";
import { test } from "node:test";
import { reconnectEditorTransport } from "./reconnectEditorTransport.ts";

test("reconnects only after stale socket closes", async () => {
    const socket = new EventTarget();
    const transportSocket = {
        readyState: Number(WebSocket.OPEN),
        addEventListener: socket.addEventListener.bind(socket),
        removeEventListener: socket.removeEventListener.bind(socket),
    };
    let connections = 0;
    let disconnections = 0;
    const cancel = reconnectEditorTransport({
        webSocket: transportSocket,
        connect: async () => {
            connections++;
        },
        disconnect: () => {
            disconnections++;
        },
    });

    assert.equal(disconnections, 1);
    assert.equal(connections, 0);
    socket.dispatchEvent(new Event("close"));
    assert.equal(connections, 1);
    socket.dispatchEvent(new Event("close"));
    assert.equal(connections, 1);
    cancel();
});

test("connects immediately without a stale socket", async () => {
    let connections = 0;
    reconnectEditorTransport({
        webSocket: null,
        connect: async () => {
            connections++;
        },
        disconnect: () => assert.fail("No socket to disconnect"),
    });
    assert.equal(connections, 1);
});

test("connects immediately when the old socket is already closed", async () => {
    const socket = new EventTarget();
    let connections = 0;
    reconnectEditorTransport({
        webSocket: {
            readyState: WebSocket.CLOSED,
            addEventListener: socket.addEventListener.bind(socket),
            removeEventListener: socket.removeEventListener.bind(socket),
        },
        connect: async () => {
            connections++;
        },
        disconnect: () => assert.fail("Closed socket must not be disconnected again"),
    });
    assert.equal(connections, 1);
});

test("connects once if socket closes during listener registration", async () => {
    const socket = new EventTarget();
    let connections = 0;
    let disconnections = 0;
    const transportSocket = {
        readyState: Number(WebSocket.OPEN),
        addEventListener: (type: "close", listener: EventListener, options?: AddEventListenerOptions) => {
            socket.addEventListener(type, listener, options);
            transportSocket.readyState = WebSocket.CLOSED;
        },
        removeEventListener: socket.removeEventListener.bind(socket),
    };
    reconnectEditorTransport({
        webSocket: transportSocket,
        connect: async () => {
            connections++;
        },
        disconnect: () => {
            disconnections++;
        },
    });
    socket.dispatchEvent(new Event("close"));
    assert.equal(connections, 1);
    assert.equal(disconnections, 0);
});

test("unmount cancels pending reconnect", async () => {
    const socket = new EventTarget();
    const transportSocket = {
        readyState: WebSocket.CLOSING,
        addEventListener: socket.addEventListener.bind(socket),
        removeEventListener: socket.removeEventListener.bind(socket),
    };
    let connections = 0;
    const cancel = reconnectEditorTransport({
        webSocket: transportSocket,
        connect: async () => {
            connections++;
        },
        disconnect: () => {},
    });
    cancel();
    socket.dispatchEvent(new Event("close"));
    assert.equal(connections, 0);
});
