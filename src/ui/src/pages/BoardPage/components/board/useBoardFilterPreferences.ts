import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { BOARD_FILTER_KEYS, type IFilterMap } from "@/core/providers/BoardProvider";

import { isBoardDeadlinePreset } from "./BoardDeadlineFilter";

export interface BoardFilterPin {
    key: keyof IFilterMap;
    value: string;
}
interface Preferences {
    revision: number;
    pinned_filters: BoardFilterPin[];
}
const queryKey = (userUID: string, projectUID: string) => ["board-filter-preferences", userUID, projectUID];
const endpoint = (projectUID: string) => `/board/${projectUID}/filter-preferences`;
function receipt(value: Preferences): Preferences {
    if (!value || !Number.isSafeInteger(value.revision) || value.revision < 0 || !Array.isArray(value.pinned_filters))
        throw new Error("Invalid filter preference receipt");
    const pins = value.pinned_filters;
    if (
        pins.length > 32 ||
        pins.some(
            (pin) =>
                !pin ||
                Object.keys(pin).length !== 2 ||
                !BOARD_FILTER_KEYS.includes(pin.key) ||
                typeof pin.value !== "string" ||
                !pin.value.trim() ||
                [...pin.value].length > 256 ||
                (pin.key === "deadlines" && !isBoardDeadlinePreset(pin.value))
        ) ||
        new Set(pins.map((pin) => JSON.stringify([pin.key, pin.value]))).size !== pins.length
    )
        throw new Error("Invalid filter preference conditions");
    return { revision: value.revision, pinned_filters: pins };
}

export default function useBoardFilterPreferences(userUID: string, projectUID: string) {
    const client = useQueryClient();
    const key = queryKey(userUID, projectUID);
    const query = useQuery({
        queryKey: key,
        queryFn: async ({ signal }) => receipt((await api.get<Preferences>(endpoint(projectUID), { signal })).data),
        enabled: !!userUID && !!projectUID,
        staleTime: 30_000,
        retry: 0,
    });
    const lock = useRef(false);
    const mutation = useMutation({
        mutationFn: async (input: { userUID: string; projectUID: string; revision: number; pins: BoardFilterPin[] }) => {
            const saved = receipt(
                (
                    await api.put<Preferences>(endpoint(input.projectUID), {
                        expected_revision: input.revision,
                        pinned_filters: input.pins,
                    })
                ).data
            );
            if (
                saved.revision !== input.revision + 1 ||
                saved.pinned_filters.length !== input.pins.length ||
                saved.pinned_filters.some((pin, index) => pin.key !== input.pins[index].key || pin.value !== input.pins[index].value)
            )
                throw new Error("Filter preference write was not confirmed");
            return saved;
        },
        // A late response updates only its original account and board.
        onSuccess: (value, input) => client.setQueryData(queryKey(input.userUID, input.projectUID), value),
        onError: (_error, input) => {
            void client.invalidateQueries({ queryKey: queryKey(input.userUID, input.projectUID) });
        },
        onSettled: () => {
            lock.current = false;
        },
        retry: 0,
    });
    const sameScope = mutation.variables?.userUID === userUID && mutation.variables?.projectUID === projectUID;
    const error = query.isError || (sameScope && mutation.isError);
    const busy = query.isFetching || mutation.isPending || !query.data || error;
    const pins = query.data?.pinned_filters ?? [];
    const isPinned = (name: keyof IFilterMap, value: string) => pins.some((pin) => pin.key === name && pin.value === value);
    return {
        pins,
        busy,
        error,
        isPinned,
        retry: () => {
            mutation.reset();
            void query.refetch();
        },
        toggle: (name: keyof IFilterMap, value: string) => {
            if (busy || lock.current || !query.data) return;
            const next = isPinned(name, value) ? pins.filter((pin) => pin.key !== name || pin.value !== value) : [...pins, { key: name, value }];
            if (next.length > 32) return;
            lock.current = true;
            mutation.mutate({ userUID, projectUID, revision: query.data.revision, pins: next });
        },
    };
}
