import { api } from "@/core/helpers/Api";
import { ENotificationType } from "@/core/models/types/notification.type";
import useAuthStore from "@/core/stores/AuthStore";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

export type TNotificationInboxState = "inbox" | "later" | "done";

interface INotificationInboxFilters {
    state?: TNotificationInboxState;
    unread_only?: boolean;
    project_uid?: string;
    reason?: ENotificationType;
}

export interface INotificationInboxThread {
    latest_event_uid: string;
    latest_event_type: ENotificationType;
    latest_at: string;
    project_uid: string | null;
    card_uid: string | null;
    card_title: string | null;
    unread_events: number;
    unread_mention: boolean;
    observed_unread_event_uids: string[];
    observed_state_event_uids: string[];
}

interface INotificationInboxPage {
    threads: INotificationInboxThread[];
    unread_count: number;
    next_cursor: string | null;
}

type TObservedInboxCommand = { event_uids: string[] } & (
    | { action: "read" }
    | { action: "state"; expected_state: TNotificationInboxState; state: TNotificationInboxState }
);

const INBOX_API = "/notifications/inbox";
export const notificationInboxKey = (actorUID: string | undefined) => ["notification-inbox", actorUID] as const;

export const useNotificationInbox = (filters: INotificationInboxFilters = {}, enabled = true) => {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    return useInfiniteQuery({
        queryKey: [
            ...notificationInboxKey(actorUID),
            { state: filters.state ?? "inbox", unread_only: filters.unread_only ?? false, project_uid: filters.project_uid, reason: filters.reason },
        ] as const,
        enabled: !!actorUID && enabled,
        initialPageParam: null as string | null,
        queryFn: async ({ signal, pageParam, queryKey: [, , params] }) => {
            const { data } = await api.get<INotificationInboxPage>(INBOX_API, { signal, params: { ...params, cursor: pageParam } });
            return data;
        },
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        staleTime: 15_000,
        retry: false,
    });
};

export const useObservedInboxCommand = () => {
    const queryClient = useQueryClient();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    return useMutation({
        retry: false,
        mutationFn: async ({ action, ...payload }: TObservedInboxCommand) => {
            if (!actorUID || actorUID !== useAuthStore.getState().currentUser?.uid) {
                throw new Error("Notification inbox session changed");
            }
            await api.put(`${INBOX_API}/${action === "read" ? "read-events" : "state"}`, payload);
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey: notificationInboxKey(actorUID) }),
    });
};
