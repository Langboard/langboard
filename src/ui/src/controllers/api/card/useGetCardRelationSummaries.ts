import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { CanceledError, isAxiosError } from "axios";
import { useEffect } from "react";
import { ESocketTopic } from "@langboard/core/enums";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import useCardRelationshipsUpdatedHandlers from "@/controllers/socket/card/useCardRelationshipsUpdatedHandlers";
import {
    acquireWorkspaceSummaryTopics,
    resetWorkspaceSummaryQueries,
    workspaceSummaryAuthScope,
} from "@/controllers/api/WorkspaceSummaryInvalidation";

export interface IRelationCardSummary {
    uid: string;
    project_uid: string;
    title: string;
    project_title?: string;
}

export interface ICardRelationSummary {
    relationship: {
        uid: string;
        parent_card_uid: string;
        child_card_uid: string;
        relationship_type_uid: string;
        parent_name: string;
        child_name: string;
        machine_semantic: string | null;
        affects_readiness: boolean;
        is_active: boolean;
        is_system_default: boolean;
    };
    target: IRelationCardSummary;
}

export interface ICardRelationSummariesPage {
    card: IRelationCardSummary;
    items: ICardRelationSummary[];
    next_cursor: string | null;
    limit: number;
}

export const relationAuthScope = workspaceSummaryAuthScope;

export default function useGetCardRelationSummaries(projectUID: string, cardUID: string) {
    const client = useQueryClient();
    const authScope = useAuthStore(relationAuthScope);
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const queryKey = ["card-relation-summaries", authScope, projectUID, cardUID];
    useEffect(() => {
        if (!actorUID || !projectUID) return;
        const socket = useSocketOutsideProvider();
        let active = true;
        const open = {
            event: "open" as const,
            eventKey: `card-relation-summary-open-${authScope}-${projectUID}`,
            callback: (event: Event) => {
                if (active && relationAuthScope(useAuthStore.getState()) === authScope) {
                    resetWorkspaceSummaryQueries(client, "card-relation-summaries", projectUID, event);
                }
            },
        };
        socket.on(open);
        const off = useCardRelationshipsUpdatedHandlers({ projectUID, summaryOnly: true }).on();
        const release = acquireWorkspaceSummaryTopics(socket, [[ESocketTopic.Board, projectUID]]);
        return () => {
            active = false;
            socket.off(open);
            off();
            release();
        };
    }, [client, actorUID, authScope, projectUID]);
    const result = useInfiniteQuery({
        queryKey,
        initialPageParam: null as string | null,
        queryFn: async ({ signal, pageParam }) => {
            // Keep the summary local to this panel; never hydrate incomplete native card models.
            const response = await api
                .get<ICardRelationSummariesPage>(`/board/${encodeURIComponent(projectUID)}/card/${encodeURIComponent(cardUID)}/relation-summaries`, {
                    signal,
                    timeout: 15_000,
                    params: { limit: 25, cursor: pageParam },
                    env: { interceptToast: true } as never,
                })
                .catch((error: unknown) => {
                    if (
                        !signal.aborted &&
                        relationAuthScope(useAuthStore.getState()) === authScope &&
                        isAxiosError(error) &&
                        [401, 403, 404].includes(error.response?.status ?? 0)
                    ) {
                        client.setQueryData(queryKey, { pages: [], pageParams: [] });
                    }
                    throw error;
                });
            if (signal.aborted || relationAuthScope(useAuthStore.getState()) !== authScope) {
                throw new CanceledError("Relationship context changed");
            }
            return response.data;
        },
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        enabled: !!actorUID && !!projectUID && !!cardUID,
        gcTime: 0,
        retry: false,
        refetchOnWindowFocus: false,
    });
    const denied = isAxiosError(result.error) && [401, 403, 404].includes(result.error.response?.status ?? 0);
    return { ...result, data: actorUID && !denied && result.data?.pages.length ? result.data : undefined };
}
