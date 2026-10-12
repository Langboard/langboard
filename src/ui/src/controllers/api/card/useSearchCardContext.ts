import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";

export interface CardSearchContext {
    uid: string;
    title: string;
    project_column_name: string;
    document_matches?: { attachment_uid: string; filename: string; snippet: string }[];
}

export default function useSearchCardContext(projectUID: string | undefined, queryText: string, enabled: boolean) {
    const { query } = useQueryMutation();
    return query<{ cards: CardSearchContext[] }>(
        ["card-context-search", projectUID, queryText],
        async ({ signal }) => {
            if (!projectUID) throw new Error("Board is required for card search");
            const response = await api.get(`/board/${encodeURIComponent(projectUID)}/cards/context`, {
                params: { input_value: queryText },
                signal,
                timeout: 15000,
            });
            return response.data;
        },
        { enabled: enabled && !!projectUID && queryText.length >= 2 && queryText.length <= 1000, retry: 0, refetchOnWindowFocus: false }
    );
}
