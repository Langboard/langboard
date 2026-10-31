import { ProjectCard, ProjectCardAttachment, ProjectCardComment, ProjectChecklist, ProjectCheckitem, MetadataModel } from "@/core/models";
import useSocketStore from "@/core/stores/SocketStore";
import { subscribedCallback } from "@/core/stores/socket/registry";
import { ESocketTopic } from "@langboard/core/enums";
import { installNativeSocketFixture } from "../wiki-summary-tests/nativeSocket.fixture";

let retained: ProjectCard.TModel | undefined;
let loadedChildren: Array<{ model: { uid: string }; current: () => unknown }> = [];

export function installLoadedCardProbe() {
    if (!retained) throw new Error("Missing retained card probe");
    retained.description = { content: "Loaded detail survives" };
    const base = { card_uid: retained.uid, created_at: new Date(), updated_at: new Date() };
    const attachment = ProjectCardAttachment.Model.fromOne({ ...base, uid: "loaded-attachment", name: "Loaded file", url: "", order: 0 } as never);
    const comment = ProjectCardComment.Model.fromOne({
        ...base,
        uid: "loaded-comment",
        content: { content: "Loaded comment" },
        is_edited: false,
        reactions: {},
    } as never);
    const checklist = ProjectChecklist.Model.fromOne({
        ...base,
        uid: "loaded-checklist",
        title: "Loaded list",
        order: 0,
        is_checked: false,
    } as never);
    const checkitem = ProjectCheckitem.Model.fromOne({
        ...base,
        uid: "loaded-checkitem",
        checklist_uid: "loaded-checklist",
        title: "Loaded item",
        order: 0,
        is_checked: false,
    } as never);
    const metadata = MetadataModel.Model.fromOne({ ...base, uid: retained.uid, type: "card", metadata: { loaded: "yes" } } as never);
    loadedChildren = [
        { model: attachment, current: () => ProjectCardAttachment.Model.getModel(attachment.uid) },
        { model: comment, current: () => ProjectCardComment.Model.getModel(comment.uid) },
        { model: checklist, current: () => ProjectChecklist.Model.getModel(checklist.uid) },
        { model: checkitem, current: () => ProjectCheckitem.Model.getModel(checkitem.uid) },
        { model: metadata, current: () => MetadataModel.Model.getModel(metadata.uid) },
    ];
}

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
        description: ProjectCard.Model.getModel(retained.uid)?.description,
        loadedChildren: loadedChildren.map(({ model, current }) => ({ uid: model.uid, same: current() === model })),
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
