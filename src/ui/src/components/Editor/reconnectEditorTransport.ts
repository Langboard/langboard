type EditorTransport = {
    webSocket: {
        readyState: number;
        addEventListener: (type: "close", listener: EventListener, options?: AddEventListenerOptions) => void;
        removeEventListener: (type: "close", listener: EventListener) => void;
    } | null;
    connect: () => Promise<unknown>;
    disconnect: () => void;
};

export function reconnectEditorTransport(transport: EditorTransport): () => void {
    const socket = transport.webSocket;
    if (!socket || socket.readyState === WebSocket.CLOSED) {
        void transport.connect().catch(() => {});
        return () => {};
    }

    let active = true;
    let reconnectRequested = false;
    const onClose = () => {
        if (active && !reconnectRequested) {
            reconnectRequested = true;
            void transport.connect().catch(() => {});
        }
    };
    socket.addEventListener("close", onClose, { once: true });
    if (socket.readyState === WebSocket.CLOSED) onClose();
    else if (!reconnectRequested) transport.disconnect();
    return () => {
        active = false;
        socket.removeEventListener("close", onClose);
    };
}
