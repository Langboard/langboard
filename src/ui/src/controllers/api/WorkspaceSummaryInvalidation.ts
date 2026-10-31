import type { QueryClient } from "@tanstack/react-query";
import type useAuthStore from "@/core/stores/AuthStore";
import type { ISocketContext } from "@/core/providers/SocketProvider";
import type { ESocketTopic } from "@langboard/core/enums";

export const workspaceSummaryAuthScope = (state: ReturnType<typeof useAuthStore.getState>) =>
    `${state.currentUser?.uid ?? ""}:${state.getIdentityVersion()}:${state.getSessionVersion()}`;

const handledEvents = new WeakMap<object, WeakMap<QueryClient, Set<string>>>();

export function resetWorkspaceSummaryQueries(
    client: QueryClient,
    kind: "wiki-summaries" | "card-relation-summaries",
    projectUID: string,
    event: object,
    cardUID?: string
) {
    // One native event can reach duplicate consumers and multiple summary scopes on the shared connection.
    const clients = handledEvents.get(event) ?? new WeakMap<QueryClient, Set<string>>();
    const scopes = clients.get(client) ?? new Set<string>();
    const scope = JSON.stringify([kind, projectUID, cardUID ?? null]);
    if (scopes.has(scope)) return;
    scopes.add(scope);
    clients.set(client, scopes);
    handledEvents.set(event, clients);
    // resetQueries cancels old reads and clears every cached page synchronously before refetching active observers.
    void client.resetQueries({
        queryKey: [kind],
        predicate: ({ queryKey }) => queryKey[2] === projectUID && (!cardUID || queryKey[3] === cardUID),
    });
}

type SummarySocket = Pick<ISocketContext, "subscribe" | "unsubscribe" | "isSubscribed" | "on" | "off">;
type SummaryTopic = Exclude<ESocketTopic, ESocketTopic.None | ESocketTopic.Global>;
const topics = new Map<string, { references: number; release: () => void }>();

export function acquireWorkspaceSummaryTopics(socket: SummarySocket, requested: readonly (readonly [SummaryTopic, string])[]) {
    const releases = requested.map(([topic, topicId]) => {
        const key = `${topic}:${topicId}`;
        let owned = topics.get(key);
        if (!owned) {
            const borrowed = socket.isSubscribed(topic, topicId);
            const subscribe = () => socket.subscribe(topic, [topicId]);
            const open = { event: "open" as const, eventKey: `workspace-summary-topic-${key}`, callback: subscribe };
            socket.on(open);
            owned = {
                references: 0,
                release: () => {
                    socket.off(open);
                    if (!borrowed) socket.unsubscribe(topic, [topicId]);
                },
            };
            topics.set(key, owned);
            if (!borrowed) subscribe();
        }
        const lease = owned;
        lease.references++;
        return () => {
            if (--lease.references) return;
            topics.delete(key);
            lease.release();
        };
    });
    return () => releases.forEach((release) => release());
}
