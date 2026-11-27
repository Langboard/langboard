import { SOCKET_URL } from "@/constants";
import { ESocketTopic } from "@langboard/core/enums";
import {
    canCloseSocket,
    getSocket,
    getSocketMap,
    isSocketOpenOrConnecting,
    isSocketTopicWithoutId,
    resetSocketConnectionState,
    setSocket,
} from "@/core/stores/socket/state";
import { queueSubscribedCallback, queueUnsubscribedCallback } from "@/core/stores/socket/registry";
import type { ISocketCreateSocketProps, ISocketStore } from "@/core/stores/socket/types";

const clearSocketHandlers = (socket: WebSocket) => {
    socket.onopen = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
};

const clearSocketQueueTimeout = () => {
    const socketMap = getSocketMap();

    if (!socketMap.sendingQueueTimeout) {
        return;
    }

    clearTimeout(socketMap.sendingQueueTimeout);
    delete socketMap.sendingQueueTimeout;
};

const scheduleSocketQueueFlush = () => {
    const socketMap = getSocketMap();

    clearSocketQueueTimeout();
    socketMap.sendingQueueTimeout = setTimeout(flushSocketQueue, 300);
};

const sendTopicMessage = (
    send: ISocketStore["send"],
    event: "subscribe" | "unsubscribe",
    topic: Exclude<ESocketTopic, ESocketTopic.None | ESocketTopic.Global>,
    topicIds: string[]
) => {
    send(
        JSON.stringify({
            event,
            topic,
            topic_id: topicIds,
        })
    );
};

const restoreTopicSubscriptions = (subscribe: ISocketStore["subscribe"], subscriptions: ReturnType<typeof getSocketMap>["subscriptions"]) => {
    const subscriptionEntries = Object.entries(subscriptions);
    for (let i = 0; i < subscriptionEntries.length; ++i) {
        const [topic, topicMap] = subscriptionEntries[i];

        if (!topicMap || isSocketTopicWithoutId(topic as ESocketTopic)) {
            continue;
        }

        subscribe(topic as Exclude<ESocketTopic, ESocketTopic.None | ESocketTopic.Global>, Object.keys(topicMap));
    }
};

const notifyDisconnectedTopicNotifiers = () => {
    const socketMap = getSocketMap();
    const topicNotifierMaps = Object.values(socketMap.subscribedTopicNotifiers);

    for (let i = 0; i < topicNotifierMaps.length; ++i) {
        const topicNotifierMap = topicNotifierMaps[i];

        if (!topicNotifierMap) {
            continue;
        }

        const topicNotifierEntries = Object.entries(topicNotifierMap);
        for (let j = 0; j < topicNotifierEntries.length; ++j) {
            const [topicId, notifiers] = topicNotifierEntries[j];
            const notifierValues = Object.values(notifiers);

            for (let k = 0; k < notifierValues.length; ++k) {
                notifierValues[k](topicId, false);
            }
        }
    }
};

const handleSubscriptionResponse = (
    response: Record<string, unknown>,
    onSubscribed: (topic: ESocketTopic, topicIds: string[]) => void,
    onUnsubscribed: (topic: ESocketTopic, topicIds: string[]) => void
) => {
    if (response.event !== "subscribed" && response.event !== "unsubscribed") {
        return false;
    }

    const topic = response.topic as ESocketTopic;
    const topicIds = response.topic_id as string[];

    if (!topic || !topicIds) {
        return true;
    }

    if (response.event === "subscribed") {
        onSubscribed(topic, topicIds);
        return true;
    }

    onUnsubscribed(topic, topicIds);
    return true;
};

export const createAuthorizedWebSocketUrl = (accessToken: string, path: string = "") => {
    const socketUrl = new URL(SOCKET_URL);
    if (socketUrl.protocol === "http:") {
        socketUrl.protocol = "ws:";
    } else if (socketUrl.protocol === "https:") {
        socketUrl.protocol = "wss:";
    }

    if (path) {
        const normalizedPath = path.replace(/^\/+/, "");
        const basePath = socketUrl.pathname.replace(/\/+$/, "");
        socketUrl.pathname = `${basePath}/${normalizedPath}`.replace(/\/{2,}/g, "/");
    }

    socketUrl.searchParams.set("authorization", accessToken);

    return socketUrl.toString();
};

export const createSocketConnection = <TResponse>({
    props,
    subscribe,
    onSubscribed,
    onUnsubscribed,
}: {
    props: ISocketCreateSocketProps<TResponse>;
    subscribe: ISocketStore["subscribe"];
    onSubscribed: (topic: ESocketTopic, topicIds: string[]) => void;
    onUnsubscribed: (topic: ESocketTopic, topicIds: string[]) => void;
}) => {
    const { accessToken, onOpen, onMessage, onError, onClose } = props;
    const currentSocket = getSocket();

    if (currentSocket) {
        if (isSocketOpenOrConnecting(currentSocket)) {
            return currentSocket;
        }

        clearSocketHandlers(currentSocket);
    }

    const nextSocket = new WebSocket(createAuthorizedWebSocketUrl(accessToken));
    setSocket(nextSocket);
    restoreTopicSubscriptions(subscribe, getSocketMap().subscriptions);

    nextSocket.onopen = async (event) => {
        await onOpen(event);
    };

    nextSocket.onmessage = async (event) => {
        if (!event.data) {
            return;
        }

        const response = JSON.parse(event.data);

        if (!handleSubscriptionResponse(response, onSubscribed, onUnsubscribed)) {
            await onMessage(response);
            return;
        }
    };

    nextSocket.onerror = async (event) => {
        await onError(event);
    };

    nextSocket.onclose = async (event) => {
        await onClose(event);
    };

    return nextSocket;
};

export const flushSocketQueue = () => {
    const socketMap = getSocketMap();
    const currentSocket = getSocket();

    clearSocketQueueTimeout();

    if (
        !socketMap.sendingQueue.length ||
        !currentSocket ||
        (currentSocket.readyState !== WebSocket.OPEN && currentSocket.readyState !== WebSocket.CONNECTING)
    ) {
        return;
    }

    if (currentSocket.readyState !== WebSocket.OPEN) {
        scheduleSocketQueueFlush();
        return;
    }

    while (socketMap.sendingQueue.length > 0) {
        const json = socketMap.sendingQueue.shift()!;
        currentSocket.send(json);
    }
};

export const sendSocketMessage = (json: string) => {
    const socketMap = getSocketMap();
    const currentSocket = getSocket();

    if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
        socketMap.sendingQueue.push(json);
        scheduleSocketQueueFlush();
        return true;
    }

    currentSocket.send(json);
    return true;
};

export const closeSocketConnection = () => {
    const currentSocket = getSocket();

    notifyDisconnectedTopicNotifiers();
    clearSocketQueueTimeout();
    resetSocketConnectionState();

    if (currentSocket) {
        clearSocketHandlers(currentSocket);
    }

    if (canCloseSocket(currentSocket)) {
        currentSocket?.close();
    }

    setSocket(null);
};

export const subscribeToTopics = (
    send: ISocketStore["send"],
    topic: Exclude<ESocketTopic, ESocketTopic.None | ESocketTopic.Global>,
    topicIds: string[],
    callback?: () => void
) => {
    if (!getSocket()) {
        return;
    }

    queueSubscribedCallback(topic, topicIds, callback);
    sendTopicMessage(send, "subscribe", topic, topicIds);
};

export const unsubscribeFromTopics = (
    send: ISocketStore["send"],
    topic: Exclude<ESocketTopic, ESocketTopic.None | ESocketTopic.Global>,
    topicIds: string[],
    callback?: () => void
) => {
    const socketMap = getSocketMap();

    if (!getSocket() || !socketMap.subscriptions[topic]) {
        return;
    }

    for (let i = 0; i < topicIds.length; ++i) {
        const topicId = topicIds[i];

        if (socketMap.subscriptions[topic][topicId]) {
            delete socketMap.subscriptions[topic][topicId];
        }
    }

    queueUnsubscribedCallback(topic, topicIds, callback);
    sendTopicMessage(send, "unsubscribe", topic, topicIds);
};
