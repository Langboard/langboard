import { api } from "@/core/helpers/Api";
import { useEffect, useState } from "react";
import { isAxiosError } from "axios";

export interface DirectoryGroup {
    uid: string;
    display_name: string;
    source: string;
    issuer: string;
    external_id: string | null;
    source_status: string;
    managed: boolean;
    read_only: boolean;
    abilities: { read_members: boolean; update: boolean; delete: boolean; manage_members: boolean };
}
export interface DirectoryMember {
    uid: string;
    firstname: string;
    lastname: string;
    username: string;
    account_status: "active" | "inactive" | "deleted";
}
interface DirectoryPage<T> {
    records: T[];
    total_records: number;
    group?: DirectoryGroup;
}

// Request identity prevents a render with rows from the previous search/page/selection.
export default function useGroupDirectory<T>(path: string, page: number, query: string) {
    const [revision, setRevision] = useState(0);
    const identity = JSON.stringify([path, page, query, revision]);
    const [result, setResult] = useState<{ identity: string; data?: DirectoryPage<T>; error?: number }>();
    useEffect(() => {
        const abort = new AbortController();
        api.get<DirectoryPage<T>>(path, {
            signal: abort.signal,
            params: { page: Math.min(1000000, Math.max(1, page)), limit: 15, query: query.slice(0, 200) },
            env: { interceptToast: true } as never,
        })
            .then(({ data }) => {
                if (!abort.signal.aborted) setResult({ identity, data });
            })
            .catch((error: unknown) => {
                if (!abort.signal.aborted) setResult({ identity, error: isAxiosError(error) ? error.response?.status || 500 : 500 });
            });
        return () => abort.abort();
    }, [identity, path, page, query]);
    const current = result?.identity === identity ? result : undefined;
    return { data: current?.data, error: current?.error, loading: !current, retry: () => setRevision((value) => value + 1) };
}
