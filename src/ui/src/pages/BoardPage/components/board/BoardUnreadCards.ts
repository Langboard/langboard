import { useId, useMemo, useSyncExternalStore } from "react";
import { ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";

export const isUnreadNativeCard = (card: ProjectCard.TModel, projectUID: string) =>
    card.project_uid === projectUID && card.has_unread_change && card.source_type !== "project_wiki" && card.source_type !== "project_card";

export default function useBoardUnreadCards(projectUID: string, snapshotUpdatedAt: number): ProjectCard.TModel[] {
    const key = useId();
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    // Cross-board transfers invalidate both card snapshots; rebind incoming models on that snapshot.
    const store = useMemo(() => {
        const ready = !!actor && !!snapshotUpdatedAt;
        const read = () => (ready ? ProjectCard.Model.getModels((card) => isUnreadNativeCard(card, projectUID)) : []);
        let snapshot = read();
        return {
            getSnapshot: () => snapshot,
            subscribe(listener: () => void) {
                if (!ready) return () => {};
                let fieldCleanup: (() => void)[] = [];
                const refresh = () => {
                    snapshot = read();
                    listener();
                };
                const bind = () => {
                    fieldCleanup.forEach((off) => off());
                    fieldCleanup = ProjectCard.Model.getModels((card) => card.project_uid === projectUID).map((card) =>
                        card.subscribeFields(
                            [
                                "project_uid",
                                "has_unread_change",
                                "source_type",
                                "last_change_seq",
                                "last_change_at",
                                "last_change_target_type",
                                "title",
                            ],
                            refresh
                        )
                    );
                    refresh();
                };
                // Creation/deletion alone misses read receipts and socket updates on existing models.
                const offCreation = ProjectCard.Model.subscribe("CREATION", key, bind, (card) => card.project_uid === projectUID);
                const offDeletion = ProjectCard.Model.subscribe("DELETION", key, bind);
                bind();
                return () => {
                    offCreation();
                    offDeletion();
                    fieldCleanup.forEach((off) => off());
                };
            },
        };
    }, [projectUID, snapshotUpdatedAt, actor, session, key]);
    return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
