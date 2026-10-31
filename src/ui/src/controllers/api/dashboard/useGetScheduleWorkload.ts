import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import type { ScheduleRange } from "./useGetScheduleCalendar";

export interface ScheduleWorkload {
    scope: "board" | "personal";
    project_uid: string | null;
    start: string;
    end: string;
    as_of: string;
    time_attribution: "all_work_on_selected_cards";
    time_includes_currently_completed_and_archived_cards: true;
    workload: { open_count: number; incomplete_count: number };
    time: {
        item_count: number;
        known_invested_seconds: number;
        unknown_item_count: number;
        invested_seconds: number | null;
        running_item_count: number | null;
        evidence: "complete" | "incomplete";
    };
}

export default function useGetScheduleWorkload(range: ScheduleRange, enabled: boolean) {
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const token = useAuthStore((state) => state.getToken());
    const key = JSON.stringify([actor, session, token, range.project_uid, range.scope, range.start, range.end, enabled]);
    const currentKey = useRef(key);
    currentKey.current = key;
    const isCurrent = () => {
        const auth = useAuthStore.getState();
        return currentKey.current === key && auth.currentUser?.uid === actor && auth.getSessionVersion() === session && auth.getToken() === token;
    };
    const query = useQuery({
        queryKey: ["schedule-workload", actor, session, range.project_uid, range.scope, range.start, range.end, enabled],
        enabled: enabled && !!actor,
        queryFn: async ({ signal }) => {
            const response = await api.get<ScheduleWorkload>("/dashboard/schedule/workload", {
                params: { ...range, project_uid: range.scope === "board" ? range.project_uid : undefined },
                signal,
                env: { interceptToast: true } as never,
            });
            if (!isCurrent()) throw new DOMException("Schedule context changed", "AbortError");
            return response.data;
        },
        retry: false,
        gcTime: 0,
        staleTime: 0,
    });
    return { ...query, data: enabled && isCurrent() && !query.isError ? query.data : undefined };
}
