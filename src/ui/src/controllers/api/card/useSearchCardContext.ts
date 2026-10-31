import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { type TQueryOptions, useQueryMutation } from "@/core/helpers/QueryMutation";

export interface CardDocumentMatch {
    attachment_uid: string;
    filename: string;
    snippet: string;
    chunk_id?: string;
    generation?: string;
    content_hash?: string;
    page?: number;
    pages?: number[];
    provenance_scope?: string;
}

export interface CardSearchContext {
    uid: string;
    title: string;
    project_column_name: string;
    document_matches?: CardDocumentMatch[];
}

export default function useSearchCardContext(
    projectUID: string | undefined,
    queryText: string,
    enabled: boolean,
    options?: TQueryOptions<{ cards: CardSearchContext[] }>
) {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const { query } = useQueryMutation();
    return query<{ cards: CardSearchContext[] }>(
        ["card-context-search", projectUID, queryText, actorUID, session],
        async ({ signal }) => {
            if (!projectUID) throw new Error("Board is required for card search");
            const response = await api.get(`/board/${encodeURIComponent(projectUID)}/cards/context`, {
                params: { input_value: queryText },
                signal,
                timeout: 15000,
            });
            signal.throwIfAborted();
            const auth = useAuthStore.getState();
            if (auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session) throw new Error("Card search no longer authorized");
            return response.data;
        },
        { ...options, enabled: enabled && !!projectUID && queryText.length >= 2 && queryText.length <= 1000, retry: 0, refetchOnWindowFocus: false }
    );
}
