import type { IWorkState } from "@/core/models/ProjectCard";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";

export interface ScheduleItem {
    card_uid: string;
    title: string;
    project_uid: string;
    project_title: string;
    column_uid: string;
    column_name: string;
    deadline_at: string;
    work_state: IWorkState | undefined;
}
interface SchedulePage {
    items: ScheduleItem[];
    next_cursor: string | null;
    start: string;
    end: string;
    scope: "board" | "personal";
}
export interface ScheduleRange {
    scope: "board" | "personal";
    project_uid: string;
    start: string;
    end: string;
}

export default function useGetScheduleCalendar(range: ScheduleRange, enabled: boolean) {
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const token = useAuthStore((state) => state.getToken());
    const key = JSON.stringify([actor, session, token, range.project_uid, range.scope, range.start, range.end, enabled]);
    const currentKey = useRef(key);
    currentKey.current = key;
    const params = { ...range, project_uid: range.scope === "board" ? range.project_uid : undefined };
    const isCurrent = () => {
        const auth = useAuthStore.getState();
        return currentKey.current === key && auth.currentUser?.uid === actor && auth.getSessionVersion() === session && auth.getToken() === token;
    };
    const query = useInfiniteQuery({
        queryKey: ["schedule-calendar", actor, session, range.project_uid, range.scope, range.start, range.end, enabled],
        enabled: enabled && !!actor,
        initialPageParam: undefined as string | undefined,
        queryFn: async ({ pageParam, signal }) => {
            const response = await api.get<SchedulePage>("/dashboard/schedule", {
                params: { ...params, cursor: pageParam, limit: 50 },
                signal,
                env: { interceptToast: true } as never,
            });
            if (!isCurrent()) throw new DOMException("Schedule context changed", "AbortError");
            return response.data;
        },
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        retry: false,
        gcTime: 0,
        staleTime: 0,
    });
    const exportAbort = useRef<AbortController | undefined>(undefined);
    const [exportState, setExportState] = useState<{ key: string; pending: boolean; error: boolean }>();
    useEffect(() => {
        setExportState(undefined);
        return () => exportAbort.current?.abort();
    }, [key]);
    const download = async () => {
        exportAbort.current?.abort();
        const controller = new AbortController();
        exportAbort.current = controller;
        setExportState({ key, pending: true, error: false });
        try {
            const response = await api.get<Blob>("/dashboard/schedule/ical", {
                params,
                signal: controller.signal,
                responseType: "blob",
                env: { interceptToast: true } as never,
            });
            if (controller.signal.aborted || !isCurrent()) return;
            const url = URL.createObjectURL(response.data);
            try {
                const link = document.createElement("a");
                link.href = url;
                link.download = `schedule-${range.scope}.ics`;
                link.click();
            } finally {
                URL.revokeObjectURL(url);
            }
            setExportState({ key, pending: false, error: false });
        } catch {
            if (!controller.signal.aborted && isCurrent()) setExportState({ key, pending: false, error: true });
        }
    };
    // Read-time fencing also covers token rotation before identity hydration publishes a store update.
    const items =
        enabled && isCurrent()
            ? [...new Map(query.data?.pages.flatMap((page) => page.items).map((item) => [item.card_uid, item]) ?? []).values()]
            : [];
    return {
        ...query,
        items,
        download,
        exporting: exportState?.key === key && exportState.pending,
        exportError: exportState?.key === key && exportState.error,
    };
}
