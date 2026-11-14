import { api } from "@/core/helpers/Api";
import { queryClient } from "@/core/helpers/QueryClient";
import { ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import type { IUnreadCardSummary } from "./useGetUnreadCardSummaries";
import { isCancelledError } from "@tanstack/react-query";

const key = (projectUID: string, cardUID: string) => ["card-change-state", projectUID, cardUID];

export const cancelCardChangeState = (projectUID: string, cardUID: string) => queryClient.cancelQueries({ queryKey: key(projectUID, cardUID) });

/** Shared events carry no reader-specific counts. Fetch only this reader's changed card. */
export async function refreshCardChangeState(projectUID: string, cardUID: string) {
    const auth = useAuthStore.getState();
    const actor = auth.currentUser?.uid;
    if (!actor) return;
    const session = auth.getSessionVersion();
    const identity = auth.getIdentityVersion();
    const card = ProjectCard.Model.getModel(cardUID);
    if (card?.project_uid !== projectUID) return;
    const readVersion = card.read_state_version;
    const current = () => {
        const latest = useAuthStore.getState();
        return latest.currentUser?.uid === actor && latest.getSessionVersion() === session && latest.getIdentityVersion() === identity;
    };
    await cancelCardChangeState(projectUID, cardUID);
    if (!current()) return;
    try {
        await queryClient.fetchQuery({
            queryKey: [...key(projectUID, cardUID), actor, session, identity],
            retry: 0,
            gcTime: 0,
            queryFn: async ({ signal }) => {
                const response = await api.get<{
                    card_uid: string;
                    change_state: Pick<
                        IUnreadCardSummary,
                        "has_unread_change" | "unread_change_count" | "unread_change_count_status" | "latest_change"
                    >;
                }>(`/board/${encodeURIComponent(projectUID)}/card/${encodeURIComponent(cardUID)}/read-state`, {
                    signal,
                    env: { interceptToast: true } as never,
                });
                signal.throwIfAborted();
                const state = response.data?.change_state;
                if (
                    response.data?.card_uid !== cardUID ||
                    !state ||
                    typeof state.has_unread_change !== "boolean" ||
                    !(state.unread_change_count === null || (Number.isSafeInteger(state.unread_change_count) && state.unread_change_count >= 0)) ||
                    !["exact", "unknown_history", "manual_unread", "not_applicable"].includes(state.unread_change_count_status) ||
                    !Number.isSafeInteger(state.latest_change?.seq) ||
                    state.latest_change.seq < 0
                ) {
                    throw new Error("Invalid card change state");
                }
                if (
                    current() &&
                    ProjectCard.Model.getModel(cardUID) === card &&
                    card.project_uid === projectUID &&
                    card.read_state_version === readVersion &&
                    state.latest_change.seq >= (card.last_change_seq ?? 0)
                ) {
                    ProjectCard.Model.fromOne<typeof ProjectCard.Model, ProjectCard.Interface>({
                        uid: card.uid,
                        created_at: card.created_at,
                        updated_at: card.updated_at,
                        has_unread_change: state.has_unread_change,
                        unread_change_count: state.unread_change_count,
                        unread_change_count_status: state.unread_change_count_status as ProjectCard.Interface["unread_change_count_status"],
                        last_change_seq: state.latest_change.seq,
                        last_change_target_type: state.latest_change.target_type ?? undefined,
                        last_change_target_uid: state.latest_change.target_uid ?? undefined,
                        last_change_at: state.latest_change.at ? new Date(state.latest_change.at) : undefined,
                    });
                }
                return response.data;
            },
        });
    } catch (error) {
        if (isCancelledError(error)) return;
        if (current() && ProjectCard.Model.getModel(cardUID) === card && card.read_state_version === readVersion) {
            // Old servers, failed reads and revoked cards retain the existing authorized board recovery path.
            void queryClient.invalidateQueries({
                queryKey: [`get-cards-${projectUID}`, { project_uid: projectUID }, actor, session],
                exact: true,
            });
        }
    }
}
