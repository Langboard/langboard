import type { ReactNode } from "react";
// Transport-only fixture. Production authentication and ACL are separate gates.
const socket = {
    getAuthorizedWebSocketUrl: () => "ws://127.0.0.1:4243?authorization=fixture-only",
    isConnected: () => true,
    on: () => {},
    off: () => {},
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
