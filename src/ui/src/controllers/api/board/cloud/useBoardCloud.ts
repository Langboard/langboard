import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";

export type CloudView = "all" | "recent" | "files" | "media" | "links";
export interface CloudItem {
    id: string;
    attachment_uid?: string;
    can_replace?: boolean;
    name: string;
    url: string;
    media_type: string;
    kind: "file" | "media" | "link";
    folder_id: string;
    created_at: string;
    updated_at: string;
    source: {
        kind?: "project" | "card" | "wiki";
        project_uid: string;
        card_uid?: string;
        wiki_uid?: string;
        title: string;
        board_url: string;
        url: string;
    };
}
export interface CloudPage {
    items: CloudItem[];
    folders: { id: string; name: string }[];
    total: number | null;
    total_is_exact: boolean;
    recent_limit: number;
    has_more: boolean;
    next_cursor: string | null;
    capabilities: { hyperlinks: boolean; upload: boolean; replace: boolean; all_recent?: "attachments_only" | "attachments_and_links" };
    limitations?: { reason: "source_too_large"; source: CloudItem["source"] }[];
    limitation_count?: number;
}

function isSafeLink(value: unknown) {
    if (typeof value !== "string" || value.length > 2048 || /[\s\\]/.test(value) || [...value].some((char) => char.charCodeAt(0) < 32)) return false;
    try {
        const url = new URL(value);
        return ["http:", "https:"].includes(url.protocol) && !!url.hostname && !url.username && !url.password;
    } catch {
        return false;
    }
}

export default function useBoardCloud(projectUID: string, view: CloudView, enabled = true) {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    return useInfiniteQuery({
        queryKey: ["board-cloud", actorUID, identity, projectUID, view],
        initialPageParam: null as string | null,
        queryFn: async ({ signal, pageParam }): Promise<CloudPage> => {
            const { data } = await api.get<CloudPage>(`/board/${encodeURIComponent(projectUID)}/cloud`, {
                signal,
                params: { view, limit: 100, ...(pageParam ? { cursor: pageParam } : {}) },
                env: { interceptToast: true } as never,
            });
            signal.throwIfAborted();
            const auth = useAuthStore.getState();
            if (auth.currentUser?.uid !== actorUID || auth.getIdentityVersion() !== identity) throw new Error("Cloud identity changed");
            if (
                !Array.isArray(data.items) ||
                !Array.isArray(data.folders) ||
                data.items.some((item) => item.source?.project_uid !== projectUID || (item.kind === "link" && !isSafeLink(item.url))) ||
                data.limitations?.some((item) => item.source?.project_uid !== projectUID) ||
                (data.has_more && (!data.next_cursor || data.next_cursor === pageParam))
            )
                throw new Error("Invalid cloud response");
            return data;
        },
        getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_cursor : undefined),
        enabled: !!actorUID && !!projectUID && enabled,
        retry: false,
        staleTime: 0,
    });
}
