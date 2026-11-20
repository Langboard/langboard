import { api } from "@/core/helpers/Api";
import { isAxiosError } from "axios";
import { useEffect, useState } from "react";

export interface UserBusinessActivityRecord {
    event_id: string;
    occurred_at: string;
    actor_principal_uid: string;
    action: string;
    project: { uid: string; title: string };
    resource: { type: "card" | "wiki"; uid: string; title: string } | null;
}
interface UserBusinessActivityPage {
    records: UserBusinessActivityRecord[];
    page: number;
    limit: number;
    has_more: boolean;
}

export interface BusinessActivityFilters {
    project_uid?: string;
    occurred_from?: string;
    occurred_before?: string;
}

export default function useUserBusinessActivity(uid: string, page: number, filters: BusinessActivityFilters) {
    const [revision, setRevision] = useState(0);
    const identity = JSON.stringify([uid, page, filters, revision]);
    const [result, setResult] = useState<{ identity: string; data?: UserBusinessActivityPage; error?: number }>();
    useEffect(() => {
        const abort = new AbortController();
        api.get<UserBusinessActivityPage>(`/settings/users/${uid}/business-activity`, {
            signal: abort.signal,
            params: { page, limit: 20, ...filters },
            env: { interceptToast: true } as never,
        })
            .then(({ data }) => {
                if (!abort.signal.aborted) setResult({ identity, data });
            })
            .catch((error: unknown) => {
                if (!abort.signal.aborted) setResult({ identity, error: isAxiosError(error) ? error.response?.status || 500 : 500 });
            });
        return () => abort.abort();
    }, [uid, page, filters, identity]);
    const current = result?.identity === identity ? result : undefined;
    return { data: current?.data, error: current?.error, loading: !current, retry: () => setRevision((value) => value + 1) };
}
