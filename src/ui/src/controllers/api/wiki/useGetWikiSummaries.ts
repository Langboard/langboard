import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useRef } from "react";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { workspaceSummaryAuthScope } from "@/controllers/api/WorkspaceSummaryInvalidation";
import useWikiSummaryEvents from "@/controllers/socket/wiki/useWikiSummaryEvents";

export interface IWikiSummaryPage {
    items: { wiki_uid: string; title: string; is_public: boolean }[];
    next_cursor: string | null;
    limit: number;
}

export default function useGetWikiSummaries(projectUID: string) {
    const client = useQueryClient();
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const authScope = useAuthStore(workspaceSummaryAuthScope);
    const token = useAuthStore((state) => state.getToken());
    const key = JSON.stringify([authScope, token, projectUID]);
    const queryKey = ["wiki-summaries", authScope, projectUID];
    const currentKey = useRef(key);
    currentKey.current = key;
    const isCurrent = () => {
        const auth = useAuthStore.getState();
        return currentKey.current === key && workspaceSummaryAuthScope(auth) === authScope && auth.getToken() === token;
    };
    const result = useInfiniteQuery({
        queryKey,
        enabled: !!actor && !!projectUID,
        initialPageParam: null as string | null,
        queryFn: async ({ signal, pageParam }) => {
            const response = await api
                .get<IWikiSummaryPage>(`/board/${projectUID}/wikis/summaries`, {
                    params: { limit: 25, cursor: pageParam ?? undefined },
                    signal,
                    timeout: 15_000,
                    env: { interceptToast: true } as never,
                })
                .catch((error: unknown) => {
                    if (!signal.aborted && isCurrent() && isAxiosError(error) && [401, 403, 404].includes(error.response?.status ?? 0)) {
                        client.setQueryData(queryKey, { pages: [], pageParams: [] });
                    }
                    throw error;
                });
            if (signal.aborted || !isCurrent()) throw new DOMException("Wiki context changed", "AbortError");
            return response.data;
        },
        getNextPageParam: (page) => page.next_cursor,
        retry: false,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
    });
    const denied = isAxiosError(result.error) && [401, 403, 404].includes(result.error.response?.status ?? 0);
    const data = actor && isCurrent() && !denied && result.data?.pages.length ? result.data : undefined;
    useWikiSummaryEvents(
        projectUID,
        authScope,
        data?.pages.flatMap((page) => page.items.filter((wiki) => !wiki.is_public).map((wiki) => wiki.wiki_uid)) ?? []
    );
    return { ...result, data };
}
