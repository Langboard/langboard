import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { ESocketTopic } from "@langboard/core/enums";

export interface IUnreadCardSummary {
    uid: string;
    project_uid: string;
    title: string;
    has_unread_change: boolean;
    unread_change_count: number | null;
    unread_change_count_status: string;
    latest_change: { seq: number; target_type: string | null; target_uid: string | null; at: string | null };
}
interface IUnreadCardSummaryPage {
    items: IUnreadCardSummary[];
    next_cursor: string | null;
    limit: number;
}

export default function useGetUnreadCardSummaries(projectUID: string) {
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const identity = JSON.stringify([projectUID, actor, session]);
    const current = useRef(identity);
    current.current = identity;
    const [page, setPage] = useState<{ identity: string; cursor: string | null }>({ identity, cursor: null });
    const cursor = page.identity === identity ? page.cursor : null;
    const client = useQueryClient();
    const socket = useSocketOutsideProvider();
    const key = useId();
    const queryKey = ["unread-card-summaries", projectUID, actor, session];
    const result = useQuery({
        queryKey: [...queryKey, cursor],
        enabled: !!actor,
        retry: 0,
        gcTime: 0,
        refetchOnMount: "always",
        refetchOnReconnect: "always",
        refetchOnWindowFocus: false,
        queryFn: async ({ signal }): Promise<IUnreadCardSummaryPage> => {
            const response = await api.get<IUnreadCardSummaryPage>(`/board/${encodeURIComponent(projectUID)}/unread-card-summaries`, {
                signal,
                params: { limit: 50, ...(cursor ? { cursor } : {}) },
            });
            signal.throwIfAborted();
            const auth = useAuthStore.getState();
            if (current.current !== identity || auth.currentUser?.uid !== actor || auth.getSessionVersion() !== session) {
                throw new Error("Unread summary request no longer authorized");
            }
            const data = response.data;
            if (
                !data ||
                !Array.isArray(data.items) ||
                data.items.length > 50 ||
                (data.next_cursor !== null && typeof data.next_cursor !== "string") ||
                data.items.some(
                    (row) =>
                        !row ||
                        typeof row.uid !== "string" ||
                        !row.uid ||
                        row.project_uid !== projectUID ||
                        typeof row.title !== "string" ||
                        row.has_unread_change !== true ||
                        !row.latest_change ||
                        !Number.isSafeInteger(row.latest_change.seq)
                )
            ) {
                throw new Error("Invalid unread summary response");
            }
            return data;
        },
    });

    useEffect(() => {
        if (!actor) return;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const refresh = () => {
            // Coalesce one native mutation's receipts, model updates and board invalidations.
            clearTimeout(timer);
            timer = setTimeout(() => {
                if (current.current !== identity) return;
                setPage({ identity, cursor: null });
                void client.invalidateQueries({ queryKey: ["unread-card-summaries", projectUID, actor, session] });
            }, 0);
        };
        const onBoardEvent = ({ event }: { event: string }) => {
            if (
                /^board:card:(details:changed|created|deleted|moved|order:changed):/.test(event) ||
                /^board:(details:changed|assigned-users:updated|roles:user:updated|deleted|column:(deleted|archive:toggled)):/.test(event)
            )
                refresh();
        };
        const boardEvents = { topic: ESocketTopic.Board as const, topicId: projectUID, event: "*", eventKey: key, callback: onBoardEvent };
        const onPrivateEvent = (message: { event: string; data?: { project_uid?: string } }) => {
            if (message.event === "user:project-roles:updated") {
                if (message.data?.project_uid === projectUID) refresh();
                return;
            }
            onBoardEvent(message);
        };
        const privateEvents = { topic: ESocketTopic.UserPrivate as const, topicId: actor, event: "*", eventKey: key, callback: onPrivateEvent };
        const opened = { event: "open" as const, eventKey: key, callback: refresh };
        socket.on(boardEvents);
        socket.on(privateEvents);
        socket.on(opened);
        const offQuery = client.getQueryCache().subscribe((event) => {
            if (
                event.type === "updated" &&
                event.action.type === "invalidate" &&
                (event.query.queryKey[0] === `get-cards-${projectUID}` ||
                    (event.query.queryKey[0] === "card-read-state" && event.query.queryKey[1] === projectUID))
            )
                refresh();
        });
        return () => {
            clearTimeout(timer);
            socket.off(boardEvents);
            socket.off(privateEvents);
            socket.off(opened);
            offQuery();
        };
    }, [projectUID, actor, session, identity, key, client]);

    return {
        ...result,
        // A failed authority refresh must not keep displaying revoked cached rows.
        data: actor && !result.isError ? result.data : undefined,
        more: () => {
            if (result.data?.next_cursor) setPage({ identity, cursor: result.data.next_cursor });
        },
    };
}
