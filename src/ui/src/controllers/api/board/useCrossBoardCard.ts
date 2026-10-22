import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useQueryClient } from "@tanstack/react-query";

export interface CrossBoardRequest {
    source_project_uid: string;
    source_card_uid: string;
    destination_column_uid: string;
    operation: "link" | "clone";
    request_uid: string;
    confirmation_token?: string;
}
export interface CrossBoardResult {
    confirmation_required?: boolean;
    confirmation_token?: string;
    created: boolean;
    excluded?: { member_uids: string[]; label_uids: string[] };
}

export default function useCrossBoardCard(projectUID: string) {
    const queryClient = useQueryClient();
    return async (request: CrossBoardRequest, signal: AbortSignal): Promise<CrossBoardResult> => {
        const auth = useAuthStore.getState();
        const actor = auth.currentUser?.uid;
        const session = auth.getSessionVersion();
        if (!actor) throw new Error("Cross-board request no longer authorized");
        const response = await api.post(`/board/${encodeURIComponent(projectUID)}/card/cross-board`, request, {
            signal,
            validateStatus: (status) => (status >= 200 && status < 300) || status === 409,
            env: { interceptToast: true } as never,
        });
        signal.throwIfAborted();
        const current = useAuthStore.getState();
        if (current.currentUser?.uid !== actor || current.getSessionVersion() !== session)
            throw new Error("Cross-board request no longer authorized");
        if (response.status === 409) {
            if (
                request.operation !== "clone" ||
                response.data?.confirmation_required !== true ||
                typeof response.data.confirmation_token !== "string"
            )
                throw new Error("Cross-board conflict");
            return { confirmation_required: true, confirmation_token: response.data.confirmation_token, created: false };
        }
        if (!response.data?.card?.uid || response.data.confirmation_required) throw new Error("Invalid cross-board response");
        await queryClient.invalidateQueries({ queryKey: [`get-cards-${projectUID}`] });
        return { created: !!response.data.created, excluded: response.data.excluded };
    };
}
