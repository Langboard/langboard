import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { isAxiosError } from "axios";
import { useEffect, useState } from "react";

export interface SignInHistoryFilters {
    outcome: "all" | "success" | "failure";
    occurred_from?: string;
    occurred_before?: string;
}
interface SignInHistoryPage {
    records: { event_id: string; occurred_at: string; is_success: boolean; error_code: string | null }[];
    page: number;
    limit: number;
    has_more: boolean;
}

export default function useUserSignInHistory(uid: string, page: number, filters: SignInHistoryFilters) {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const [revision, setRevision] = useState(0);
    const identity = JSON.stringify([actorUID, sessionVersion, uid, page, filters, revision]);
    const [result, setResult] = useState<{ identity: string; data?: SignInHistoryPage; error?: number }>();
    useEffect(() => {
        if (!actorUID) return;
        const abort = new AbortController();
        const current = () =>
            !abort.signal.aborted &&
            actorUID === useAuthStore.getState().currentUser?.uid &&
            sessionVersion === useAuthStore.getState().getSessionVersion();
        api.get<SignInHistoryPage>(`/settings/users/${uid}/sign-in-history`, {
            signal: abort.signal,
            params: { page, limit: 20, ...filters },
            env: { interceptToast: true } as never,
        })
            .then(({ data }) => {
                if (current()) setResult({ identity, data });
            })
            .catch((error: unknown) => {
                if (current()) setResult({ identity, error: isAxiosError(error) ? error.response?.status || 500 : 500 });
            });
        return () => abort.abort();
    }, [actorUID, sessionVersion, uid, page, filters, identity]);
    const current = actorUID && result?.identity === identity ? result : undefined;
    return {
        data: current?.data,
        error: !actorUID ? 401 : current?.error,
        loading: !!actorUID && !current,
        retry: () => setRevision((value) => value + 1),
    };
}
