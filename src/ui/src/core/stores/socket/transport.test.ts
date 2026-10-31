import assert from "node:assert/strict";
import { test } from "node:test";
import { ESocketTopic, SOCKET_MAX_TOPIC_IDS } from "@langboard/core/enums";
import * as connectionState from "./state";
import { getSocketMap, resetSocketConnectionState, setSocket } from "./state";
import { subscribedCallback, unsubscribedCallback } from "./registry";
import { closeSocketConnection, createSocketConnection, subscribeToTopics, unsubscribeFromTopics } from "./transport";

test("subscription frames respect the shared limit, acknowledgements, and reconnect restoration", async () => {
    const OriginalWebSocket = globalThis.WebSocket;
    class TestSocket {
        static OPEN = 1;
        static CONNECTING = 0;
        readyState = 1;
        onopen?: (event: Event) => Promise<void>;
        send() {}
    }
    globalThis.WebSocket = TestSocket as unknown as typeof WebSocket;
    try {
        for (const count of [0, SOCKET_MAX_TOPIC_IDS, SOCKET_MAX_TOPIC_IDS + 1, 92, 129]) {
            resetSocketConnectionState();
            setSocket(new TestSocket() as unknown as WebSocket);
            const ids = Array.from({ length: count }, (_, index) => `board-${index}`);
            const frames: { event: string; topic: string; topic_id: string[] }[] = [];
            const send = (frame: string) => {
                frames.push(JSON.parse(frame));
                return true;
            };
            let subscribed = 0;
            subscribeToTopics(send, ESocketTopic.Dashboard, ids, () => ++subscribed);
            assert.equal(frames.length, Math.max(1, Math.ceil(count / SOCKET_MAX_TOPIC_IDS)));
            assert.deepEqual(
                frames.flatMap((frame) => frame.topic_id),
                ids
            );
            for (const frame of frames) {
                assert.equal(frame.event, "subscribe");
                assert.equal(frame.topic, ESocketTopic.Dashboard);
                assert.ok(frame.topic_id.length <= SOCKET_MAX_TOPIC_IDS);
                subscribedCallback(ESocketTopic.Dashboard, frame.topic_id);
            }
            assert.equal(subscribed, count);
            assert.deepEqual(getSocketMap().subscribedTopics[ESocketTopic.Dashboard] ?? [], ids);

            frames.length = 0;
            setSocket(null);
            const connection = createSocketConnection({
                props: {
                    accessToken: "test-token",
                    userUID: "test-user",
                    onOpen: async () => {},
                    onMessage: async () => {},
                    onError: async () => {},
                    onClose: async () => {},
                },
                subscribe: (topic, topicIds) => subscribeToTopics(send, topic, topicIds),
                onSubscribed: subscribedCallback,
                onUnsubscribed: unsubscribedCallback,
            });
            await (connection as unknown as TestSocket).onopen!(new Event("open"));
            assert.deepEqual(
                frames.flatMap((frame) => frame.topic_id),
                ids
            );
            assert.ok(frames.every((frame) => frame.topic_id.length <= SOCKET_MAX_TOPIC_IDS));

            frames.length = 0;
            let unsubscribed = 0;
            unsubscribeFromTopics(send, ESocketTopic.Dashboard, ids, () => ++unsubscribed);
            assert.deepEqual(
                frames.flatMap((frame) => frame.topic_id),
                ids
            );
            for (const frame of frames) {
                assert.equal(frame.event, "unsubscribe");
                assert.ok(frame.topic_id.length <= SOCKET_MAX_TOPIC_IDS);
                unsubscribedCallback(ESocketTopic.Dashboard, frame.topic_id);
            }
            assert.equal(unsubscribed, count);
            assert.equal(getSocketMap().restorableTopics[ESocketTopic.Dashboard], undefined);
            assert.equal(getSocketMap().subscribedTopics[ESocketTopic.Dashboard], undefined);
        }
    } finally {
        setSocket(null);
        resetSocketConnectionState();
        globalThis.WebSocket = OriginalWebSocket;
    }
});

test("connection status follows real events and ignores replaced socket callbacks", async () => {
    const original = globalThis.WebSocket;
    class TestSocket {
        static OPEN = 1;
        static CONNECTING = 0;
        static CLOSING = 2;
        static CLOSED = 3;
        readyState = 0;
        onopen?: (event: Event) => Promise<void>;
        onerror?: (event: Event) => Promise<void>;
        onclose?: (event: CloseEvent) => Promise<void>;
        onmessage?: (event: MessageEvent) => Promise<void>;
        close() {
            this.readyState = 3;
        }
        send() {}
    }
    globalThis.WebSocket = TestSocket as unknown as typeof WebSocket;
    let callbacks = 0;
    let pendingOpen: Promise<void> | undefined;
    let restores = 0;
    const create = (userUID = "actor-a", accessToken = "token-a") =>
        createSocketConnection({
            props: {
                userUID,
                accessToken,
                onOpen: async () => {
                    callbacks++;
                    await pendingOpen;
                },
                onError: async () => {
                    callbacks++;
                },
                onClose: async () => {
                    callbacks++;
                },
                onMessage: async () => {
                    callbacks++;
                },
            },
            subscribe: () => {
                restores++;
            },
            onSubscribed: subscribedCallback,
            onUnsubscribed: unsubscribedCallback,
        }) as unknown as TestSocket;
    try {
        closeSocketConnection();
        const states: string[] = [];
        const unsubscribe = connectionState.subscribeSocketConnectionStatus(() => states.push(connectionState.getSocketConnectionStatus().status));
        const first = create();
        assert.equal(connectionState.getSocketConnectionStatus().status, "connecting");
        first.readyState = 1;
        await first.onopen!(new Event("open"));
        assert.equal(connectionState.getSocketConnectionStatus().status, "open");
        assert.deepEqual(getSocketMap().subscribedTopics, {});
        const oldClose = first.onclose!;
        const oldMessage = first.onmessage!;
        await first.onerror!(new Event("error"));
        assert.equal(connectionState.getSocketConnectionStatus().status, "error");
        first.readyState = 3;
        await first.onclose!({ code: 1006 } as CloseEvent);
        assert.equal(connectionState.getSocketConnectionStatus().status, "closed");
        const second = create();
        second.readyState = 1;
        await second.onopen!(new Event("open"));
        const before = callbacks;
        await oldClose({ code: 1006 } as CloseEvent);
        await oldMessage({ data: JSON.stringify({ event: "changed" }) } as MessageEvent);
        assert.equal(callbacks, before);
        assert.equal(connectionState.getSocketConnectionStatus().status, "open");
        getSocketMap().restorableTopics[ESocketTopic.Board] = ["board-a"];
        getSocketMap().subscribedTopics[ESocketTopic.Board] = ["board-a"];
        const renewed = create("actor-a", "new-token");
        assert.deepEqual(getSocketMap().restorableTopics[ESocketTopic.Board], ["board-a"]);
        assert.deepEqual(getSocketMap().subscribedTopics, {});
        renewed.readyState = 1;
        let releaseOpen!: () => void;
        pendingOpen = new Promise<void>((resolve) => {
            releaseOpen = resolve;
        });
        const opening = renewed.onopen!(new Event("open"));
        const other = create("actor-b", "token-b");
        const beforeRestore = restores;
        releaseOpen();
        await opening;
        assert.equal(restores, beforeRestore);
        assert.deepEqual(getSocketMap().restorableTopics, {});
        pendingOpen = undefined;
        assert.notEqual(other, second);
        assert.equal(connectionState.getSocketConnectionStatus().userUID, "actor-b");
        assert.equal(connectionState.getSocketConnectionStatus().status, "connecting");
        const latest = create("actor-b", "renewed-token");
        assert.notEqual(latest, other);
        const registry = getSocketMap();
        registry.restorableTopics[ESocketTopic.Board] = ["retained-board"];
        const subscriptions = registry.subscriptions;
        registry.sendingQueue.push("old-session-command");
        closeSocketConnection({ preserveSubscriptions: true });
        assert.equal(latest.readyState, TestSocket.CLOSED);
        assert.equal(latest.onmessage, null);
        assert.equal(registry.subscriptions, subscriptions);
        assert.deepEqual(registry.restorableTopics[ESocketTopic.Board], ["retained-board"]);
        assert.deepEqual(registry.subscribedTopics, {});
        assert.deepEqual(registry.sendingQueue, []);
        assert.deepEqual(registry.subscribedCallbackQueue, {});
        let pendingFrames = 0;
        subscribeToTopics(
            () => {
                pendingFrames++;
                return true;
            },
            ESocketTopic.Board,
            ["pending-board"]
        );
        assert.equal(pendingFrames, 0);
        assert.deepEqual(registry.restorableTopics[ESocketTopic.Board], ["retained-board", "pending-board"]);
        const changedActor = create("actor-c", "confirmed-other-user");
        assert.notEqual(changedActor, latest);
        assert.deepEqual(registry.restorableTopics, {});
        closeSocketConnection();
        subscribeToTopics(() => true, ESocketTopic.Board, ["logged-out-board"]);
        assert.deepEqual(registry.restorableTopics, {});
        assert.deepEqual(connectionState.getSocketConnectionStatus(), { status: "disconnected" });
        unsubscribe();
        assert.ok(states.includes("error") && states.includes("closed") && states.includes("open"));
    } finally {
        closeSocketConnection();
        globalThis.WebSocket = original;
    }
});
