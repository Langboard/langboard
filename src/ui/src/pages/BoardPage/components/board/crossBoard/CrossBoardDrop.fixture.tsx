import { ProjectCard } from "@/core/models";
import useSocketStore from "@/core/stores/SocketStore";
import { subscribedCallback } from "@/core/stores/socket/registry";
import { ESocketTopic } from "@langboard/core/enums";
import { installNativeSocketFixture } from "../wiki-summary-tests/nativeSocket.fixture";

let retained: ProjectCard.TModel | undefined;

function notifierKeys() {
    const notifiers = useSocketStore.getState().getStore().subscribedTopicNotifiers;
    return Object.entries(notifiers).flatMap(([topic, scopes]) =>
        Object.entries(scopes).flatMap(([scope, keys]) => Object.keys(keys).map((key) => [topic, scope, key].map(String).join(":")))
    );
}

function notifierDelta(before: string[]) {
    const after = notifierKeys();
    return { added: after.filter((key) => !before.includes(key)).length, removed: before.filter((key) => !after.includes(key)).length };
}

/** Replace only the wire; retain the native runtime, registry, handlers and model. */
export function installRetainedCardProbe(uid: string) {
    retained = ProjectCard.Model.getModel(uid);
    if (!retained) throw new Error("Missing original card model");
    installNativeSocketFixture();
    useSocketStore.getState().subscribe(ESocketTopic.Board, ["left", "right"]);
    useSocketStore.getState().subscribe(ESocketTopic.BoardCard, [uid]);
    const topics = useSocketStore.getState().getStore().restorableTopics;
    for (const [topic, ids] of Object.entries(topics)) subscribedCallback(topic as ESocketTopic, ids ?? []);
}

export function retainedCardSnapshot() {
    if (!retained) throw new Error("Missing retained card probe");
    const store = useSocketStore.getState().getStore();
    const callbacks = Object.entries(store.subscriptions).flatMap(([topic, scopes]) =>
        Object.entries(scopes).flatMap(([scope, events]) =>
            Object.entries(events).flatMap(([event, keys]) =>
                Object.entries((keys ?? {}) as Record<string, unknown[]>).flatMap(([key, handlers]) =>
                    String(event).includes(retained!.uid) ? [{ topic, scope, event: String(event), key, count: handlers.length }] : []
                )
            )
        )
    );
    return {
        same: ProjectCard.Model.getModel(retained.uid) === retained,
        uid: retained.uid,
        project: retained.project_uid,
        column: retained.project_column_uid,
        title: retained.title,
        canCrossBoard: retained.can_cross_board,
        sourceType: retained.source_type ?? null,
        labels: retained.labels.map((label) => label.uid),
        count: ProjectCard.Model.getModels((card) => card.uid === retained!.uid).length,
        callbacks,
        notifierCount: Object.values(store.subscribedTopicNotifiers).reduce(
            (sum, scopes) => sum + Object.values(scopes).reduce((total, notifiers) => total + Object.keys(notifiers).length, 0),
            0
        ),
    };
}

export function rereadRetainedCard(update: Record<string, unknown>) {
    if (!retained) throw new Error("Missing retained card probe");
    const before = notifierKeys();
    ProjectCard.Model.fromOne({ uid: retained.uid, created_at: retained.created_at, updated_at: retained.updated_at, ...update } as never);
    return { ...retainedCardSnapshot(), notifierDelta: notifierDelta(before) };
}

export function deleteRetainedCard() {
    if (!retained) throw new Error("Missing retained card probe");
    const before = notifierKeys();
    ProjectCard.Model.deleteModel(retained.uid);
    return { ...retainedCardSnapshot(), notifierDelta: notifierDelta(before) };
}
