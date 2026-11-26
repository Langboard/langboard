import { api } from "@/core/helpers/Api";
import { UserNotification } from "@/core/models";
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
    snoozable_reminder_event_uid?: string | null;
    latest_at: string;
    project_uid: string | null;
    card_uid: string | null;
    card_title: string | null;
    project_title?: string | null;
    card_column?: string | null;
    card_priority?: string | null;
    subscription_reasons?: string[];
    actor_name?: string | null;
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

export const notificationThreadKey = (row: INotificationInboxThread) => row.card_uid ?? row.latest_event_uid;

export const getInboxRows = (pages: INotificationInboxPage[] = []) => {
    const rows = new Map<string, INotificationInboxThread>();
    for (const page of pages) {
        for (const row of page.threads) {
            const key = notificationThreadKey(row);
            if (!rows.has(key)) rows.set(key, row);
        }
    }
    return [...rows.values()];
};

type TObservedInboxCommand = { event_uids: string[] } & (
    | { action: "read" }
    | { action: "state"; expected_state: TNotificationInboxState; state: TNotificationInboxState }
);

const INBOX_API = "/notifications/inbox";
export const notificationInboxKey = (actorUID: string | undefined) => ["notification-inbox", actorUID] as const;

export const useNotificationInbox = (filters: INotificationInboxFilters = {}) => {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    return useInfiniteQuery({
        queryKey: [
            ...notificationInboxKey(actorUID),
            { state: filters.state ?? "inbox", unread_only: filters.unread_only ?? false, project_uid: filters.project_uid, reason: filters.reason },
        ] as const,
        enabled: !!actorUID,
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

export const getInboxActivity = async (eventUIDs: readonly string[], signal?: AbortSignal) => {
    const params = new URLSearchParams();
    eventUIDs.forEach((uid) => params.append("event_uids", uid));
    const { data } = await api.get<{ notifications: UserNotification.Interface[] }>(`${INBOX_API}/events`, { signal, params });
    return data.notifications;
};

export const useInboxActivity = (eventUIDs: readonly string[]) => {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    return useInfiniteQuery({
        queryKey: [...notificationInboxKey(actorUID), "activity", eventUIDs] as const,
        enabled: !!actorUID && !!eventUIDs.length,
        initialPageParam: 0,
        queryFn: ({ signal, pageParam }) => getInboxActivity(eventUIDs.slice(pageParam, pageParam + 30), signal),
        getNextPageParam: (_page, _pages, offset) => (offset + 30 < eventUIDs.length ? offset + 30 : undefined),
        retry: false,
    });
};

export const reminderSnoozeMinutes = [10, 60, 1440] as const;

export const useSnoozeReminder = () => {
    const queryClient = useQueryClient();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    return useMutation({
        retry: false,
        mutationFn: async ({ uid, minutes }: { uid: string; minutes: (typeof reminderSnoozeMinutes)[number] }) => {
            if (!actorUID || actorUID !== useAuthStore.getState().currentUser?.uid) {
                throw new Error("Notification inbox session changed");
            }
            await api.put(`${INBOX_API}/events/${encodeURIComponent(uid)}/snooze`, {
                minutes,
                request_uid: crypto.randomUUID(),
                expected_snoozed_until: null,
            });
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey: notificationInboxKey(actorUID) }),
    });
};
