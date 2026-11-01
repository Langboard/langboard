import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";

export interface ProjectCollection {
    uid: string;
    name: string;
    board_uids: string[];
    starred?: boolean;
}
interface CollectionReceipt {
    revision: number;
    collections: ProjectCollection[];
}
const key = (userUID: string) => ["project-collections", userUID];
const endpoint = "/account/project-collections";
function receipt(value: CollectionReceipt): CollectionReceipt {
    if (!value || !Number.isSafeInteger(value.revision) || value.revision < 0 || !Array.isArray(value.collections))
        throw new Error("Invalid project collection receipt");
    if (value.collections.length > 64 || new Set(value.collections.map((item) => item.uid)).size !== value.collections.length)
        throw new Error("Invalid project collections");
    for (const item of value.collections) {
        if (!item || typeof item.uid !== "string" || typeof item.name !== "string" || !item.name.trim() || !Array.isArray(item.board_uids))
            throw new Error("Invalid project collection");
        if (item.starred !== undefined && typeof item.starred !== "boolean") throw new Error("Invalid collection favorite");
        if (item.board_uids.some((uid) => typeof uid !== "string" || !/^[a-zA-Z0-9]{11}$/.test(uid))) throw new Error("Invalid board reference");
    }
    return { ...value, collections: value.collections.map((item) => ({ ...item, starred: item.starred ?? false })) };
}

export default function useProjectCollections(userUID: string) {
    const client = useQueryClient();
    const lock = useRef(false);
    const query = useQuery({
        queryKey: key(userUID),
        queryFn: async ({ signal }) => {
            const value = receipt((await api.get<CollectionReceipt>(endpoint, { signal })).data);
            signal.throwIfAborted();
            if (useAuthStore.getState().currentUser?.uid !== userUID) throw new Error("Project collection account changed");
            return value;
        },
        enabled: !!userUID,
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        retry: 0,
    });
    const mutation = useMutation({
        mutationFn: async (input: { userUID: string; revision: number; collections: ProjectCollection[] }) => {
            if (useAuthStore.getState().currentUser?.uid !== input.userUID) throw new Error("Project collection account changed");
            const saved = receipt(
                (
                    await api.put<CollectionReceipt>(endpoint, {
                        expected_revision: input.revision,
                        collections: input.collections,
                    })
                ).data
            );
            if (saved.revision !== input.revision + 1 || JSON.stringify(saved.collections) !== JSON.stringify(input.collections))
                throw new Error("Project collection save was not confirmed");
            return saved;
        },
        onSuccess: (value, input) => client.setQueryData(key(input.userUID), value),
        onError: (_error, input) => {
            void client.invalidateQueries({ queryKey: key(input.userUID) });
        },
        onSettled: () => {
            lock.current = false;
        },
        retry: 0,
    });
    return {
        collections: query.data?.collections ?? [],
        loading: query.isLoading,
        error: query.isError,
        busy: query.isFetching || mutation.isPending || !query.data,
        retry: query.refetch,
        save: async (collections: ProjectCollection[]) => {
            if (!userUID || lock.current || query.isFetching || !query.data) throw new Error("Project collections are not ready");
            lock.current = true;
            return mutation.mutateAsync({
                userUID,
                revision: query.data.revision,
                collections: collections.map((item) => ({ ...item, starred: item.starred ?? false })),
            });
        },
    };
}
