import { createSocketRuntime } from "@/core/providers/socket/runtime";
import useSocketStore from "@/core/stores/SocketStore";
import { setSocket } from "@/core/stores/socket/state";
import { subscribedCallback, unsubscribedCallback } from "@/core/stores/socket/registry";
import { ESocketTopic } from "@langboard/core/enums";

// Replace only the wire. Events still pass through the native registry, runtime and converters.
export function installNativeSocketFixture() {
    const store = useSocketStore.getState();
    let receive: (message: unknown) => Promise<void> | void = () => {};
    const socket = {
        readyState: WebSocket.OPEN,
        close: () => {},
        send: (raw: string) => {
            const message = JSON.parse(raw);
            if (message.event === "subscribe") subscribedCallback(message.topic, message.topic_id);
            if (message.event === "unsubscribe") unsubscribedCallback(message.topic, message.topic_id);
        },
    } as WebSocket;
    setSocket(socket);
    createSocketRuntime({
        createSocket: (props) => {
            receive = (message) => props.onMessage(message as never);
            return socket;
        },
        getStore: store.getStore,
        closeSocket: store.close,
        getAccessToken: () => "fixture-token",
        getUserUID: () => "fixture-user",
        removeAccessToken: () => {},
        reconnect: () => {},
        shouldReconnect: () => false,
    }).connect();
    Object.assign(window, {
        emitNativeSummaryEvent: (topic: ESocketTopic, topicID: string, event: string, data: unknown) =>
            receive({ topic, topic_id: topicID, event, data }),
    });
}
