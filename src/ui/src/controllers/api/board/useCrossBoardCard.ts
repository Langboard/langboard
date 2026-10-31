import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useQueryClient } from "@tanstack/react-query";
import { ProjectCard } from "@/core/models";

export interface CrossBoardRequest {
    source_project_uid: string;
    source_card_uid: string;
    destination_column_uid: string;
    operation: "link" | "clone" | "move";
    request_uid: string;
    confirmation_token?: string;
}
export interface CrossBoardResult {
    confirmation_required?: boolean;
    confirmation_token?: string;
    created: boolean;
    moved?: boolean;
    excluded?: { member_uids: string[]; label_uids: string[] };
}
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const nonempty = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const exclusions = (value: unknown): value is NonNullable<CrossBoardResult["excluded"]> =>
    record(value) &&
    Array.isArray(value.member_uids) &&
    value.member_uids.every(nonempty) &&
    Array.isArray(value.label_uids) &&
    value.label_uids.every(nonempty);

export default function useCrossBoardCard(projectUID: string) {
    const queryClient = useQueryClient();
    return async (request: CrossBoardRequest, signal: AbortSignal): Promise<CrossBoardResult> => {
        const auth = useAuthStore.getState();
        const actor = auth.currentUser?.uid;
        const session = auth.getSessionVersion();
        if (!actor) throw new Error("Cross-board request no longer authorized");
        const response = await api.post<unknown>(`/board/${encodeURIComponent(projectUID)}/card/cross-board`, request, {
            signal,
            validateStatus: (status) => (status >= 200 && status < 300) || status === 409,
            env: { interceptToast: true } as never,
        });
        signal.throwIfAborted();
        const current = useAuthStore.getState();
        if (current.currentUser?.uid !== actor || current.getSessionVersion() !== session)
            throw new Error("Cross-board request no longer authorized");
        const source = ProjectCard.Model.getModel(request.source_card_uid);
        if (
            !source ||
            source.source_type ||
            source.can_cross_board !== true ||
            (source.project_uid !== request.source_project_uid && !(request.operation === "move" && source.project_uid === projectUID))
        )
            throw new Error("Cross-board source no longer eligible");
        const data = response.data;
        if (!record(data) || (data.excluded !== undefined && !exclusions(data.excluded))) throw new Error("Invalid cross-board response");
        if (response.status === 409) {
            if (request.operation === "link" || data.confirmation_required !== true || !nonempty(data.confirmation_token))
                throw new Error("Cross-board conflict");
            return { confirmation_required: true, confirmation_token: data.confirmation_token, created: false };
        }
        if (!record(data.card) || !nonempty(data.card.uid) || data.confirmation_required) throw new Error("Invalid cross-board response");
        if (request.operation === "move") {
            if (
                data.card.uid !== request.source_card_uid ||
                data.card.project_uid !== projectUID ||
                !nonempty(data.card.project_column_uid) ||
                (data.moved === true && data.card.project_column_uid !== request.destination_column_uid) ||
                typeof data.moved !== "boolean" ||
                !exclusions(data.excluded)
            )
                throw new Error("Invalid move response");
            // Relocate the existing model before source reconciliation can remove it.
            // Never delete the stable card identity shared by both panes.
            source.project_uid = projectUID;
            source.project_column_uid = data.card.project_column_uid;
            await Promise.all(
                [projectUID, request.source_project_uid].map((uid) => queryClient.invalidateQueries({ queryKey: [`get-cards-${uid}`] }))
            );
            return { created: false, moved: data.moved, excluded: data.excluded };
        }
        if (typeof data.created !== "boolean") throw new Error("Invalid cross-board response");
        await queryClient.invalidateQueries({ queryKey: [`get-cards-${projectUID}`] });
        return { created: data.created, excluded: data.excluded as CrossBoardResult["excluded"] };
    };
}
