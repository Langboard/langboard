import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import { ESocketTopic } from "@langboard/core/enums";
import useSocketStore, { type TSocketScopedTopic } from "@/core/stores/SocketStore";
import { createSocketRuntime } from "./runtime";

class TestSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    readyState = TestSocket.CONNECTING;
    sent: string[] = [];
    onopen: ((event: Event) => Promise<void>) | null = null;
    onmessage: ((event: MessageEvent) => Promise<void>) | null = null;
    onerror: ((event: Event) => Promise<void>) | null = null;
    onclose: ((event: CloseEvent) => Promise<void>) | null = null;

    send(json: string) {
        this.sent.push(json);
    }

    close() {
        this.readyState = TestSocket.CLOSED;
    }

    async receive(response: unknown) {
        await this.onmessage?.({ data: JSON.stringify(response) } as MessageEvent);
    }
}

const topic = ESocketTopic.BoardWiki;
const topicId = "project-a";
const unknownEvent = "wiki:newly-granted-uid:public:changed";
const knownEvent = "wiki:known-uid:title:changed";
const scopedEvent = (event = unknownEvent, data: unknown = { uid: "newly-granted-uid" }) => ({ topic, topic_id: topicId, event, data });

async function fixture(t: TestContext) {
    const originalWebSocket = globalThis.WebSocket;
    const store = useSocketStore.getState();
    store.close();
    store.getStore().defaultEvents = {};
    globalThis.WebSocket = TestSocket as unknown as typeof WebSocket;
    t.after(() => {
        store.close();
        store.getStore().defaultEvents = {};
        globalThis.WebSocket = originalWebSocket;
    });
    const identity = { userUID: "actor-a", accessToken: "token-a" };
    const runtime = createSocketRuntime({
        createSocket: store.createSocket,
        getStore: store.getStore,
        closeSocket: store.close,
        getAccessToken: () => identity.accessToken,
        getUserUID: () => identity.userUID,
        removeAccessToken: () => {},
        reconnect: () => {},
        shouldReconnect: () => false,
    });
    const connect = async () => {
        runtime.connect();
        const socket = store.getSocket() as unknown as TestSocket;
        socket.readyState = TestSocket.OPEN;
        await socket.onopen!(new Event("open"));
        return socket;
    };
    const subscribe = async (socket: TestSocket, scopedTopic: TSocketScopedTopic = topic, id = topicId) => {
        store.subscribe(scopedTopic, [id]);
        await socket.receive({ event: "subscribed", topic: scopedTopic, topic_id: [id] });
    };
    return { store, on: store.addEvent, off: store.removeEvent, identity, connect, subscribe, socket: await connect() };
}

test("scoped observers receive unknown UID payloads once alongside exact events without changing subscription frames", async (t) => {
    const { on, socket, subscribe } = await fixture(t);
    const calls: { listener: string; data: unknown }[] = [];
    on({ topic, topicId, event: "*", eventKey: "summary", callback: (data) => calls.push({ listener: "summary", data }) });
    on({ topic, topicId, event: knownEvent, eventKey: "detail", callback: (data) => calls.push({ listener: "detail", data }) });
    await subscribe(socket);

    const publicData = { uid: "newly-granted-uid", is_public: true };
    const assigneeData = { uid: "another-unknown-uid", assigned_members: ["actor-a"] };
    const knownData = { uid: "known-uid", title: "Changed title" };
    await socket.receive(scopedEvent(unknownEvent, publicData));
    await socket.receive(scopedEvent("wiki:another-unknown-uid:assigned:changed", assigneeData));
    await socket.receive(scopedEvent(knownEvent, knownData));
    await socket.receive(scopedEvent("*", "literal-star"));
    assert.deepEqual(calls, [
        { listener: "summary", data: { event: unknownEvent, data: publicData } },
        { listener: "summary", data: { event: "wiki:another-unknown-uid:assigned:changed", data: assigneeData } },
        { listener: "detail", data: knownData },
        { listener: "summary", data: { event: knownEvent, data: knownData } },
        { listener: "summary", data: { event: "*", data: "literal-star" } },
    ]);
    assert.equal((calls[3].data as { data: unknown }).data, calls[2].data, "observer envelope preserves the exact callback payload identity");
    assert.deepEqual(
        socket.sent.map((json) => JSON.parse(json)),
        [{ event: "subscribe", topic, topic_id: [topicId] }]
    );
});

test("observers require both requested and acknowledged subscriptions and stop immediately on unsubscribe", async (t) => {
    const { on, store, socket } = await fixture(t);
    let calls = 0;
    for (const event of ["*", unknownEvent]) {
        on({ topic, topicId, event, eventKey: "summary", callback: () => calls++ });
    }
    await socket.receive(scopedEvent());
    assert.equal(calls, 0, "unrequested topic");
    store.subscribe(topic, [topicId]);
    await socket.receive(scopedEvent());
    assert.equal(calls, 0, "pending acknowledgement");
    await socket.receive({ event: "unsubscribed", topic, topic_id: [topicId] });
    await socket.receive(scopedEvent());
    assert.equal(calls, 0, "denied subscription");
    await socket.receive({ event: "subscribed", topic, topic_id: [topicId] });
    await socket.receive(scopedEvent());
    assert.equal(calls, 2, "authorized exact and wildcard callbacks");

    store.unsubscribe(topic, [topicId]);
    await socket.receive(scopedEvent());
    assert.equal(calls, 2, "unsubscribe awaiting acknowledgement");
    await socket.receive({ event: "subscribed", topic, topic_id: [topicId] });
    await socket.receive(scopedEvent());
    assert.equal(calls, 2, "late acknowledgement cannot restore intent");
    await socket.receive({ event: "unsubscribed", topic, topic_id: [topicId] });
    await socket.receive(scopedEvent());
    assert.equal(calls, 2, "completed unsubscribe");
});

test("observers remain isolated by topic and project ID", async (t) => {
    const { on, socket, subscribe } = await fixture(t);
    const calls: string[] = [];
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("project-a") });
    on({ topic, topicId: "project-b", event: "*", eventKey: "summary", callback: () => calls.push("project-b") });
    await subscribe(socket);
    await subscribe(socket, topic, "project-b");
    await subscribe(socket, ESocketTopic.Board, topicId);
    await socket.receive({ ...scopedEvent(), topic: ESocketTopic.Board });
    await socket.receive({ ...scopedEvent(), topic_id: "unsubscribed-project" });
    assert.deepEqual(calls, []);
    await socket.receive({ ...scopedEvent(), topic_id: "project-b" });
    assert.deepEqual(calls, ["project-b"]);
    await socket.receive(scopedEvent());
    assert.deepEqual(calls, ["project-b", "project-a"]);
});

test("on deduplicates callbacks and off removes only the requested callback and event key", async (t) => {
    const { on, off, socket, subscribe } = await fixture(t);
    const calls: string[] = [];
    const first = { topic, topicId, event: "*", eventKey: "shared", callback: () => calls.push("first") } as const;
    const second = { ...first, callback: () => calls.push("second") };
    const other = { ...first, eventKey: "other", callback: () => calls.push("other") };
    on(first);
    on(first);
    on(second);
    on(other);
    on({ topic, topicId, event: knownEvent, eventKey: "detail", callback: () => calls.push("exact") });
    await subscribe(socket);
    await socket.receive(scopedEvent());
    assert.deepEqual(calls, ["first", "second", "other"]);
    calls.length = 0;
    off(first);
    off({ ...second, eventKey: "missing" });
    await socket.receive(scopedEvent());
    assert.deepEqual(calls, ["second", "other"]);
    calls.length = 0;
    off(second);
    off(other);
    await socket.receive(scopedEvent());
    await socket.receive(scopedEvent(knownEvent));
    assert.deepEqual(calls, ["exact"]);
});

test("implicit and default events keep exact-only dispatch, including literal stars", async (t) => {
    const { on, socket, subscribe } = await fixture(t);
    const calls: string[] = [];
    for (const implicit of [ESocketTopic.None, ESocketTopic.Global] as const) {
        on({ topic: implicit, event: "changed", eventKey: "exact", callback: () => calls.push(`${implicit}:exact`) });
        on({ topic: implicit, event: "*", eventKey: "star", callback: () => calls.push(`${implicit}:star`) });
        await socket.receive({ topic: implicit, event: "changed" });
        await socket.receive({ topic: implicit, event: "*" });
    }
    assert.deepEqual(calls, ["none:exact", "none:star", "global:exact", "global:star"]);
    calls.length = 0;
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("scoped") });
    await subscribe(socket);
    for (const event of ["open", "error", "close"] as const) {
        on({ event, eventKey: "default", callback: () => calls.push(event) });
        await socket.receive(scopedEvent(event));
    }
    assert.deepEqual(calls, ["open", "error", "close"]);
    calls.length = 0;
    await socket.onopen!(new Event("open"));
    await socket.onerror!(new Event("error"));
    await socket.onclose!({ code: 1000 } as CloseEvent);
    assert.deepEqual(calls, ["open", "error", "close"]);
});

test("replaced sockets and changed accounts cannot deliver to current observers", async (t) => {
    const { on, socket, subscribe, identity, connect } = await fixture(t);
    const calls: string[] = [];
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("actor-a") });
    await subscribe(socket);
    const oldMessage = socket.onmessage!;
    identity.userUID = "actor-b";
    identity.accessToken = "token-b";
    const nextSocket = await connect();
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("actor-b") });
    await nextSocket.receive(scopedEvent());
    assert.deepEqual(calls, []);
    await subscribe(nextSocket);
    await oldMessage({ data: JSON.stringify(scopedEvent()) } as MessageEvent);
    assert.deepEqual(calls, []);
    await nextSocket.receive(scopedEvent());
    assert.deepEqual(calls, ["actor-b"]);

    identity.accessToken = "renewed-token-b";
    const renewedSocket = await connect();
    await renewedSocket.receive(scopedEvent());
    assert.deepEqual(calls, ["actor-b"], "token renewal needs a fresh subscription acknowledgement");
    await renewedSocket.receive({ event: "subscribed", topic, topic_id: [topicId] });
    await renewedSocket.receive(scopedEvent());
    assert.deepEqual(calls, ["actor-b", "actor-b"]);
});

test("unsubscribe during an awaited exact callback blocks observer delivery", async (t) => {
    const { on, store, socket, subscribe } = await fixture(t);
    let calls = 0;
    let release!: () => void;
    let started!: () => void;
    const waiting = new Promise<void>((resolve) => (release = resolve));
    const entered = new Promise<void>((resolve) => (started = resolve));
    on({
        topic,
        topicId,
        event: knownEvent,
        eventKey: "detail",
        callback: async () => {
            started();
            await waiting;
        },
    });
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls++ });
    await subscribe(socket);
    const pending = socket.receive(scopedEvent(knownEvent));
    await entered;
    store.unsubscribe(topic, [topicId]);
    release();
    await pending;
    assert.equal(calls, 0);
});

test("account replacement during exact delivery cannot reroute the old payload to new observers", async (t) => {
    const { on, socket, subscribe, identity, connect } = await fixture(t);
    const calls: string[] = [];
    let release!: () => void;
    let started!: () => void;
    const waiting = new Promise<void>((resolve) => (release = resolve));
    const entered = new Promise<void>((resolve) => (started = resolve));
    on({
        topic,
        topicId,
        event: knownEvent,
        eventKey: "detail",
        callback: async () => {
            started();
            await waiting;
        },
    });
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("actor-a") });
    await subscribe(socket);
    const pending = socket.receive(scopedEvent(knownEvent));
    await entered;
    identity.userUID = "actor-b";
    identity.accessToken = "token-b";
    const nextSocket = await connect();
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("actor-b") });
    await subscribe(nextSocket);
    release();
    await pending;
    assert.deepEqual(calls, []);
    await nextSocket.receive(scopedEvent());
    assert.deepEqual(calls, ["actor-b"]);
});

test("observer delivery rechecks identity between awaited callbacks", async (t) => {
    const { on, socket, subscribe, identity } = await fixture(t);
    const calls: string[] = [];
    on({
        topic,
        topicId,
        event: "*",
        eventKey: "summary",
        callback: async () => {
            calls.push("first");
            identity.userUID = "actor-b";
        },
    });
    on({ topic, topicId, event: "*", eventKey: "summary", callback: () => calls.push("second") });
    await subscribe(socket);
    await socket.receive(scopedEvent());
    assert.deepEqual(calls, ["first"]);
});

for (const scoped of [true, false]) {
    for (const changed of ["user", "token"] as const) {
        test(`exact ${scoped ? "scoped" : "global"} callbacks stop after ${changed} changes during an awaited handler`, async (t) => {
            const { on, socket, subscribe, identity } = await fixture(t);
            const calls: string[] = [];
            let release!: () => void;
            let entered!: () => void;
            const waiting = new Promise<void>((resolve) => (release = resolve));
            const started = new Promise<void>((resolve) => (entered = resolve));
            const target = scoped ? { topic, topicId } : { topic: ESocketTopic.Global };
            on({
                ...target,
                event: knownEvent,
                eventKey: "first",
                callback: async () => {
                    calls.push("first");
                    entered();
                    await waiting;
                },
            });
            on({ ...target, event: knownEvent, eventKey: "second", callback: () => calls.push("second") });
            if (scoped) await subscribe(socket);
            const pending = socket.receive({ ...target, topic_id: scoped ? topicId : undefined, event: knownEvent, data: {} });
            await started;
            if (changed === "user") identity.userUID = "actor-b";
            else identity.accessToken = "token-b";
            release();
            await pending;
            assert.deepEqual(calls, ["first"]);
        });
    }
}

test("exact cleanup callbacks still finish when an earlier callback unsubscribes the same topic", async (t) => {
    const { on, store, socket, subscribe } = await fixture(t);
    const calls: string[] = [];
    on({ topic, topicId, event: knownEvent, eventKey: "remove-model", callback: () => store.unsubscribe(topic, [topicId]) });
    on({ topic, topicId, event: knownEvent, eventKey: "leave-page", callback: () => calls.push("leave-page") });
    await subscribe(socket);
    await socket.receive(scopedEvent(knownEvent));
    assert.deepEqual(calls, ["leave-page"]);
});
