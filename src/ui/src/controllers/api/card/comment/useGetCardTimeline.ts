import { api } from "@/core/helpers/Api";
import { ProjectCardComment } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import useEditorStore from "@/core/stores/EditorStore";
import type { ColumnEvent } from "@/pages/BoardPage/components/card/CardColumnEvent";
import { Routing, SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useId, useMemo, useRef } from "react";

import type { CommentCompaction, CommentCompactionEligibility } from "./CommentCompaction.types";

interface TimelineBase {
    uid: string;
    evidence_ref: string;
    created_at: string;
}
export type CardTimelineEntry = TimelineBase &
    (
        | { kind: "comment"; data: ProjectCardComment.IStore }
        | { kind: "activity"; data: ColumnEvent }
        | { kind: "compaction"; data: CommentCompaction }
    );
interface CardTimelinePage {
    records: CardTimelineEntry[];
    next_cursor: string | null;
    eligibility?: CommentCompactionEligibility;
    in_flight?: CommentCompaction[];
}

export default function useGetCardTimeline(compactionUID?: string) {
    const { projectUID, card, currentUser, socket } = useBoardCard();
    const client = useQueryClient();
    const subscriber = useId();
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const identityVersion = useAuthStore((state) => state.getIdentityVersion());
    const queryKey = useMemo(
        () => ["card-timeline", currentUser.uid, identityVersion, projectUID, card.uid, compactionUID ?? "compact"],
        [currentUser.uid, identityVersion, projectUID, card.uid, compactionUID]
    );
    const editing = useEditorStore(
        (state) => !!state.currentEditor?.startsWith(`${card.uid}-comment-`) && state.currentEditor !== `${card.uid}-comment-form`
    );
    const editingRef = useRef(editing);
    editingRef.current = editing;
    const pagingRef = useRef(false);
    const dirty = useRef(false);
    const query = useInfiniteQuery({
        queryKey,
        initialPageParam: undefined as string | undefined,
        queryFn: async ({ pageParam, signal }) => {
            const requestSession = useAuthStore.getState().getSessionVersion();
            const cardURL = Utils.String.format(Routing.API.BOARD.CARD.GET_DETAILS, { uid: projectUID, card_uid: card.uid });
            const url = compactionUID
                ? `${cardURL}/comment-compactions/${encodeURIComponent(compactionUID)}/evidence`
                : Utils.String.format(`${Routing.API.ACTIVITIY.CARD}/timeline`, { uid: projectUID, card_uid: card.uid });
            const { data } = await api.get<CardTimelinePage>(url, {
                params: { before: pageParam, ...(!compactionUID && { compact: true }) },
                signal,
            });
            const auth = useAuthStore.getState();
            if (
                signal.aborted ||
                auth.currentUser?.uid !== currentUser.uid ||
                auth.getSessionVersion() !== requestSession ||
                auth.getIdentityVersion() !== identityVersion
            ) {
                throw new DOMException("Timeline request cancelled", "AbortError");
            }
            ProjectCardComment.Model.fromArray(data.records.flatMap((entry) => (entry.kind === "comment" ? [entry.data] : [])));
            return data;
        },
        getNextPageParam: (last) => last.next_cursor ?? undefined,
        retry: false,
        staleTime: Infinity,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const refresh = useCallback(() => {
        if (editingRef.current || pagingRef.current) {
            dirty.current = true;
            return;
        }
        dirty.current = false;
        void client.invalidateQueries({ queryKey, exact: true });
    }, [client, queryKey]);

    const previousSession = useRef(sessionVersion);
    useEffect(() => {
        if (previousSession.current === sessionVersion) return;
        previousSession.current = sessionVersion;
        // Same-user credential rotation must not unmount an unsaved editor.
        refresh();
    }, [sessionVersion, refresh]);

    useEffect(
        () =>
            client.getMutationCache().subscribe((event) => {
                if (event.type !== "updated" || event.action.type !== "success") return;
                const mutation = event.mutation;
                if (!["add-card-comment", "update-card-comment", "delete-card-comment"].includes(String(mutation.options.mutationKey?.[0]))) return;
                const variables = mutation.state.variables as { project_uid?: string; card_uid?: string } | undefined;
                if (variables?.project_uid === projectUID && variables.card_uid === card.uid) refresh();
            }),
        [client, projectUID, card.uid, refresh]
    );

    useEffect(() => {
        // Do not replace/unmount an in-progress comment editor during a timeline refresh.
        if (editing && client.getQueryData(queryKey)) {
            dirty.current = true;
            void client.cancelQueries({ queryKey, exact: true });
        } else if (!editing && dirty.current) refresh();
    }, [editing, client, queryKey, refresh]);

    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        const schedule = () => {
            clearTimeout(timer);
            timer = setTimeout(refresh, 100);
        };
        const eventKey = `card-timeline-${subscriber}`;
        const activity = {
            topic: ESocketTopic.Dashboard as const,
            topicId: projectUID,
            event: SocketEvents.SERVER.DASHBOARD.PROJECT.ACTIVITY_RECORDED.replace("{uid}", projectUID),
            eventKey,
            callback: schedule,
        };
        const commentEvents = ["ADDED", "UPDATED", "DELETED"] as const;
        const comments = commentEvents.map((name) => ({
            topic: ESocketTopic.Board as const,
            topicId: projectUID,
            event: SocketEvents.SERVER.BOARD.CARD.COMMENT[name].replace("{uid}", card.uid),
            eventKey,
            callback: schedule,
        }));
        const open = { event: "open" as const, eventKey, callback: schedule };
        socket.on(activity);
        comments.forEach((listener) => socket.on(listener));
        socket.on(open);
        return () => {
            clearTimeout(timer);
            socket.off(activity);
            comments.forEach((listener) => socket.off(listener));
            socket.off(open);
        };
    }, [socket, subscriber, projectUID, card.uid, refresh]);

    const entries = useMemo(() => {
        const seen = new Set<string>();
        return (
            query.data?.pages
                .flatMap((page) => page.records)
                .filter((entry) => {
                    if (seen.has(entry.evidence_ref)) return false;
                    seen.add(entry.evidence_ref);
                    return true;
                }) ?? []
        );
    }, [query.data]);
    const fetchNextPage = useCallback<typeof query.fetchNextPage>(
        (options) => {
            // Manual paging supersedes a background refresh; reconcile its changes afterward.
            if (query.isFetching && !query.isFetchingNextPage && options?.cancelRefetch !== false) dirty.current = true;
            // Fence socket events synchronously, before React renders the fetching state.
            pagingRef.current = true;
            return query.fetchNextPage(options).finally(() => {
                pagingRef.current = false;
                if (dirty.current) refresh();
            });
        },
        [query.isFetching, query.isFetchingNextPage, query.fetchNextPage, refresh]
    );
    return {
        ...query,
        fetchNextPage,
        entries,
        refresh,
        editing,
        eligibility: query.data?.pages[0]?.eligibility,
        inFlight: query.data?.pages[0]?.in_flight ?? [],
    };
}
