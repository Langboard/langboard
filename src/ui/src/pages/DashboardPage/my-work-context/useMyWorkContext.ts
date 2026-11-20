import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useEffect, useState } from "react";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { validateMyWorkPage, type MyWorkFilter, type MyWorkOrder, type MyWorkPageResult } from "./MyWorkContext";

export default function useMyWorkContext(filter: MyWorkFilter, order: MyWorkOrder = "updated") {
    const client = useQueryClient();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const [day, setDay] = useState(() => new Date().toDateString());
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    useEffect(() => {
        const refresh = () => setDay(new Date().toDateString());
        const next = new Date();
        next.setHours(24, 0, 0, 0);
        const timer = window.setTimeout(refresh, next.getTime() - Date.now() + 50);
        window.addEventListener("focus", refresh);
        return () => {
            window.clearTimeout(timer);
            window.removeEventListener("focus", refresh);
        };
    }, [day]);
    const queryKey = ["dashboard-my-work", actorUID, identity, filter.projectUID, filter.group, zone, day, order];
    return useInfiniteQuery({
        queryKey,
        enabled: !!actorUID,
        initialPageParam: null as string | null,
        queryFn: async ({ signal, pageParam }) => {
            const auth = useAuthStore.getState();
            const session = auth.getSessionVersion();
            const isCurrent = () => {
                const current = useAuthStore.getState();
                return current.currentUser?.uid === actorUID && current.getIdentityVersion() === identity && current.getSessionVersion() === session;
            };
            const response = await api
                .get<MyWorkPageResult>(Routing.API.DASHBOARD.MY_WORK, {
                    signal,
                    params: {
                        project_uid: filter.projectUID ?? undefined,
                        group: filter.group ?? undefined,
                        timezone_name: zone,
                        cursor: pageParam ?? undefined,
                        limit: 50,
                        order,
                    },
                    env: { interceptToast: true } as never,
                })
                .catch((error: unknown) => {
                    if (!signal.aborted && isCurrent() && isAxiosError(error) && [401, 403, 404].includes(error.response?.status ?? 0)) {
                        client.setQueryData(queryKey, { pages: [], pageParams: [] });
                    }
                    throw error;
                });
            signal.throwIfAborted();
            if (!isCurrent()) {
                throw new Error("My Work request no longer belongs to the current actor");
            }
            return validateMyWorkPage(response.data, filter, order);
        },
        getNextPageParam: (last) => last.next_cursor,
        retry: false,
        staleTime: 15_000,
    });
}
