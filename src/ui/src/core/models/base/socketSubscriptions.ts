import { Utils } from "@langboard/core/utils";
import { ESocketTopic } from "@langboard/core/enums";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { getTopicWithId } from "@/core/stores/SocketStore";
import type { IModelMap } from "@/core/models/ModelRegistry";

export interface IModelSocketHandler {
    topic?: ESocketTopic;
    topicId?: string;
    on: () => () => void;
}

export type TModelSocketHandlerFactory<TProps> = (props: TProps) => IModelSocketHandler;
export type TModelSocketSubscription = [ESocketTopic, string, string, (() => void)[]];

type TModelSocketSubscriptionMap = Partial<
    Record<
        keyof IModelMap,
        {
            [uid: string]: TModelSocketSubscription[];
        }
    >
>;

const SOCKET = useSocketOutsideProvider();
const socketSubscriptions: TModelSocketSubscriptionMap = {};

export const subscribeModelSocketTopic = (topic: ESocketTopic, topicIds: string[]) => {
    SOCKET.subscribe(topic as never, topicIds);
};

export const unsubscribeModelSocketTopic = (topic: ESocketTopic, topicIds: string[]) => {
    SOCKET.unsubscribe(topic as never, topicIds);
};

export const unsubscribeModelSocketEvents = (modelName: keyof IModelMap, uid: string) => {
    if (!socketSubscriptions[modelName]?.[uid]) {
        return;
    }

    socketSubscriptions[modelName][uid].forEach(([topic, topicId, key, offs]) => {
        offs.forEach((off) => off());
        SOCKET.unsubscribeTopicNotifier({ topic, topicId: topicId as never, key });
    });

    delete socketSubscriptions[modelName][uid];
};

export const clearModelSocketSubscriptions = (modelName: keyof IModelMap) => {
    delete socketSubscriptions[modelName];
};

export const subscribeModelSocketEvents = <TProps>({
    modelName,
    uid,
    events,
    props,
}: {
    modelName: keyof IModelMap;
    uid: string;
    events: TModelSocketHandlerFactory<TProps>[];
    props: TProps;
}) => {
    if (!socketSubscriptions[modelName]) {
        socketSubscriptions[modelName] = {};
    }

    if (!socketSubscriptions[modelName][uid]) {
        socketSubscriptions[modelName][uid] = [];
    }

    const topicMap: Partial<Record<ESocketTopic, Record<string, IModelSocketHandler["on"][]>>> = {};
    const currentSubscriptions: TModelSocketSubscription[] = [];

    for (let i = 0; i < events.length; ++i) {
        const handlers = events[i](props);
        const { topic, topicId } = getTopicWithId(handlers);

        if (!topic || !topicId) {
            continue;
        }

        if (!topicMap[topic]) {
            topicMap[topic] = {};
        }

        if (!topicMap[topic][topicId]) {
            topicMap[topic][topicId] = [];
        }

        topicMap[topic][topicId].push(handlers.on);
    }

    Object.entries(topicMap).forEach(([topic, topicIdMap]) => {
        Object.entries(topicIdMap ?? {}).forEach(([topicId, handlers]) => {
            const key = Utils.String.Token.uuid();
            const offs: (() => void)[] = [];
            const subscription: TModelSocketSubscription = [topic as ESocketTopic, topicId, key, offs];

            SOCKET.subscribeTopicNotifier({
                topic: topic as ESocketTopic,
                topicId: topicId as never,
                key,
                notifier: (subscribedTopicId, isSubscribed) => {
                    if (topicId !== subscribedTopicId) {
                        return;
                    }

                    offs.forEach((off) => off());
                    offs.splice(0);

                    if (!isSubscribed) {
                        return;
                    }

                    for (let i = 0; i < handlers.length; ++i) {
                        offs.push(handlers[i]());
                    }
                },
            });

            currentSubscriptions.push(subscription);
            socketSubscriptions[modelName]![uid].push(subscription);
        });
    });

    return () => {
        if (!socketSubscriptions[modelName]?.[uid]) {
            return;
        }

        for (let i = 0; i < socketSubscriptions[modelName][uid].length; ++i) {
            const subscription = socketSubscriptions[modelName][uid][i];
            const currentSubscriptionIndex = currentSubscriptions.indexOf(subscription);

            if (currentSubscriptionIndex === -1) {
                continue;
            }

            const [topic, topicId, key, offs] = subscription;
            offs.forEach((off) => off());
            SOCKET.unsubscribeTopicNotifier({ topic, topicId: topicId as never, key });
            socketSubscriptions[modelName][uid].splice(i, 1);
            currentSubscriptions.splice(currentSubscriptionIndex, 1);
            --i;
        }

        Object.keys(topicMap).forEach((topic) => {
            delete topicMap[topic as ESocketTopic];
        });
    };
};
