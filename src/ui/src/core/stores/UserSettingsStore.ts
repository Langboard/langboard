import { useCallback, useEffect, useMemo, useState } from "react";
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { TOpenCardsByUser } from "@/pages/DashboardPage/components/OpenCardsData";

export const NOTIFICATIONS_TIME_RANGE_OPTIONS = ["3d", "7d", "1m", "all"] as const;
export interface IPinnedApp {
    key: string;
    name: string;
}
export const MAX_PINNED_APPS = 32;
export interface IUserSettings {
    column_card_sorts?: Record<string, string>;
    notifications_time_range?: (typeof NOTIFICATIONS_TIME_RANGE_OPTIONS)[number];
    graph_view_modes?: Record<string, "columns" | "network">;
    open_cards?: TOpenCardsByUser;
    explorer_open_cards_collapsed?: Record<string, boolean>;
    wiki_hint_dismissed?: Record<string, boolean>;
    workbench_context_open?: Record<string, boolean>;
    workbench_sidebar_collapsed?: Record<string, boolean>;
    pinned_apps?: Record<string, IPinnedApp[]>;
}

interface IUserSettingsStore {
    settings: IUserSettings;
    updateSettingsByKey: <TKey extends keyof IUserSettings>(key: TKey, value: IUserSettings[TKey]) => void;
    updateSettings: (settings: Partial<IUserSettings>) => void;
    replaceSettings: (settings: Partial<IUserSettings>) => void;
    deleteSettingsByKey: (key: keyof IUserSettings) => void;
}

const USER_SETTINGS_STORE_STORAGE_KEY = "user-settings-store";

const useUserSettingsStore = create(
    immer<IUserSettingsStore>((set, get) => {
        const updateStorage = () => {
            setTimeout(() => {
                localStorage.setItem(USER_SETTINGS_STORE_STORAGE_KEY, JSON.stringify(get().settings));
            }, 0);
        };

        return {
            settings: JSON.parse(localStorage.getItem(USER_SETTINGS_STORE_STORAGE_KEY) || "{}"),
            updateSettingsByKey: (key, value) => {
                set((state) => {
                    state.settings[key] = value;
                });
                updateStorage();
            },
            updateSettings: (settings) => {
                set((state) => {
                    state.settings = {
                        ...state.settings,
                        ...settings,
                    };
                });
                updateStorage();
            },
            replaceSettings: (settings) => {
                set((state) => {
                    state.settings = settings as IUserSettings;
                });
                updateStorage();
            },
            deleteSettingsByKey: (key) => {
                set((state) => {
                    delete state.settings[key];
                });
                updateStorage();
            },
        };
    })
);

export const getUserSettingsStore = () => useUserSettingsStore.getState();

export const useUserSettings = <TKey extends keyof IUserSettings>(key: TKey): IUserSettings[TKey] => {
    const [settings, setSettings] = useState(getUserSettingsStore().settings[key]);

    useEffect(() => {
        const off = useUserSettingsStore.subscribe((state) => {
            const newSettings = state.settings[key];
            if (newSettings !== settings) {
                setSettings(newSettings);
            }
        });

        return off;
    }, [settings, setSettings, key]);

    return settings;
};

export const useWorkbenchContextOpen = (userUID?: string) => {
    const saved = useUserSettings("workbench_context_open");
    const open = userUID ? (saved?.[userUID] ?? true) : true;
    const setOpen = useCallback(
        (value: boolean | ((previous: boolean) => boolean)) => {
            if (!userUID) return;
            const store = getUserSettingsStore();
            const current = store.settings.workbench_context_open ?? {};
            const previous = current[userUID] ?? true;
            store.updateSettingsByKey("workbench_context_open", {
                ...current,
                [userUID]: typeof value === "function" ? value(previous) : value,
            });
        },
        [userUID]
    );
    return [open, setOpen] as const;
};

export default useUserSettingsStore;

function normalizePinnedApps(value: unknown): IPinnedApp[] {
    if (!Array.isArray(value)) return [];
    const pins: IPinnedApp[] = [];
    const seen = new Set<string>();
    for (const item of value) {
        if (
            !item ||
            typeof item.key !== "string" ||
            !/^[a-z][a-z0-9_-]{0,31}$/.test(item.key) ||
            typeof item.name !== "string" ||
            !item.name.trim() ||
            seen.has(item.key)
        )
            continue;
        pins.push({ key: item.key, name: item.name.trim().slice(0, 80) });
        seen.add(item.key);
        if (pins.length === MAX_PINNED_APPS) break;
    }
    return pins;
}

export function usePinnedApps(userUID?: string) {
    const saved = useUserSettings("pinned_apps");
    const pins = useMemo(() => normalizePinnedApps(userUID ? saved?.[userUID] : undefined), [saved, userUID]);
    const update = useCallback(
        (change: (current: IPinnedApp[]) => IPinnedApp[]) => {
            if (!userUID) return;
            const store = getUserSettingsStore();
            const current = store.settings.pinned_apps ?? {};
            store.updateSettingsByKey("pinned_apps", {
                ...current,
                [userUID]: normalizePinnedApps(change(normalizePinnedApps(current[userUID]))),
            });
        },
        [userUID]
    );
    const toggle = useCallback(
        (app: IPinnedApp) =>
            update((current) => (current.some((pin) => pin.key === app.key) ? current.filter((pin) => pin.key !== app.key) : [...current, app])),
        [update]
    );
    const move = useCallback(
        (key: string, direction: -1 | 1) =>
            update((current) => {
                const index = current.findIndex((pin) => pin.key === key);
                const target = index + direction;
                if (index < 0 || target < 0 || target >= current.length) return current;
                const next = [...current];
                [next[index], next[target]] = [next[target], next[index]];
                return next;
            }),
        [update]
    );
    return { pins, toggle, move };
}
