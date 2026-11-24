import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { normalizePinnedApps, usePinnedApps, type IPinnedApp } from "@/core/stores/UserSettingsStore";

interface Preferences {
    revision: number;
    pinned_apps: IPinnedApp[];
}
const endpoint = "/account/app-preferences";
function receipt(value: Preferences): Preferences {
    if (!value || !Number.isSafeInteger(value.revision) || value.revision < 0 || !Array.isArray(value.pinned_apps))
        throw new Error("Invalid app preference receipt");
    const pins = normalizePinnedApps(value.pinned_apps);
    if (
        pins.length !== value.pinned_apps.length ||
        pins.some((pin, index) => {
            const item = value.pinned_apps[index];
            return Object.keys(item).length !== 2 || pin.key !== item.key || pin.name !== item.name;
        })
    )
        throw new Error("Invalid app preference shortcuts");
    return { revision: value.revision, pinned_apps: pins };
}

export default function useSyncedPinnedApps(userUID?: string) {
    const local = usePinnedApps(userUID);
    const client = useQueryClient();
    const key = ["account-app-preferences", userUID];
    const query = useQuery({
        queryKey: key,
        enabled: !!userUID,
        queryFn: async ({ signal }) => receipt((await api.get<Preferences>(endpoint, { signal })).data),
        staleTime: 30_000,
        gcTime: 300_000,
        retry: 0,
    });
    const replace = local.replace;
    const pins = query.data && query.data.revision > 0 ? query.data.pinned_apps : local.pins;
    useEffect(() => {
        // A never-saved account retains its local shortcuts until an explicit edit.
        if (query.data && query.data.revision > 0) replace(query.data.pinned_apps);
    }, [query.data, replace]);
    const lock = useRef(false);
    const mutation = useMutation({
        mutationFn: async (pinned_apps: IPinnedApp[]) => {
            if (!query.data) throw new Error("Read current app preferences before saving");
            const expected = query.data.revision;
            const saved = receipt((await api.put<Preferences>(endpoint, { expected_revision: expected, pinned_apps })).data);
            if (saved.revision !== expected + 1 || JSON.stringify(saved.pinned_apps) !== JSON.stringify(pinned_apps))
                throw new Error("App preference write was not confirmed");
            return saved;
        },
        onSuccess: (value) => client.setQueryData(key, value),
        // A timeout may have committed. Read back before allowing another write.
        onError: () => {
            void client.invalidateQueries({ queryKey: key });
        },
        onSettled: () => {
            lock.current = false;
        },
        retry: 0,
    });
    const busy = query.isFetching || mutation.isPending || !query.data || query.isError || mutation.isError;
    const update = (change: (pins: IPinnedApp[]) => IPinnedApp[]) => {
        if (!userUID || busy || lock.current) return;
        const next = normalizePinnedApps(change(pins));
        if (JSON.stringify(next) === JSON.stringify(pins)) return;
        lock.current = true;
        mutation.mutate(next);
    };
    return {
        pins,
        busy,
        error: query.isError || mutation.isError,
        retry: () => {
            mutation.reset();
            void query.refetch();
        },
        toggle: (app: IPinnedApp) =>
            update((pins) => (pins.some((pin) => pin.key === app.key) ? pins.filter((pin) => pin.key !== app.key) : [...pins, app])),
        move: (appKey: string, direction: -1 | 1) =>
            update((pins) => {
                const index = pins.findIndex((pin) => pin.key === appKey);
                const target = index + direction;
                if (index < 0 || target < 0 || target >= pins.length) return pins;
                const next = [...pins];
                [next[index], next[target]] = [next[target], next[index]];
                return next;
            }),
    };
}
