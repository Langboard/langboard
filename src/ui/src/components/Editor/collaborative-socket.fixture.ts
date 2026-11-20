import type { ReactNode } from "react";
import type { TSocketAddEventProps, TSocketRemoveEventProps } from "@/core/stores/SocketStore";

const listeners = new Map<string, Map<string, (data: unknown) => void>>();
const listenerKey = (props: { topic?: unknown; topicId?: string; event: string }) => JSON.stringify([props.topic, props.topicId, props.event]);

/** Dispatch only to registered native listeners; no network/authentication is simulated. */
export function emitCollaborativeFixtureEvent(props: { topic?: unknown; topicId?: string; event: string; data: unknown }) {
    for (const callback of [...(listeners.get(listenerKey(props))?.values() ?? [])]) callback(props.data);
}

// Transport-only fixture. Production authentication and ACL are separate gates.
const socket = {
    getAuthorizedWebSocketUrl: () => "ws://127.0.0.1:4243?authorization=fixture-only",
    isConnected: () => true,
    on: <T>(props: TSocketAddEventProps<T>) => {
        const key = listenerKey(props);
        const callbacks = listeners.get(key) ?? new Map<string, (data: unknown) => void>();
        callbacks.set(props.eventKey, props.callback as (data: unknown) => void);
        listeners.set(key, callbacks);
    },
    off: <T>(props: TSocketRemoveEventProps<T>) => {
        const key = listenerKey(props);
        const callbacks = listeners.get(key);
        callbacks?.delete(props.eventKey);
        if (!callbacks?.size) listeners.delete(key);
    },
    subscribe: () => {},
    unsubscribe: () => {},
    subscribeTopicNotifier: () => {},
    unsubscribeTopicNotifier: () => {},
    send: () => ({ isConnected: true }),
    stream: () => {},
    streamOff: () => {},
    reconnect: () => {},
    close: () => {},
    isSubscribed: () => false,
};
export const useSocket = () => socket;
export const useSocketOutsideProvider = () => socket;

export const SocketProvider = ({ children }: { children: ReactNode }) => children;
