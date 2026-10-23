import { useCallback, useMemo } from "react";
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
    child_card_collapsed?: Record<string, true>;
    column_card_sorts?: Record<string, string>;
    notifications_time_range?: (typeof NOTIFICATIONS_TIME_RANGE_OPTIONS)[number];
    graph_view_modes?: Record<string, "columns" | "network">;
    open_cards?: TOpenCardsByUser;
    explorer_open_cards_collapsed?: Record<string, boolean>;
    explorer_sections_collapsed?: Record<string, Partial<Record<"favorites" | "recent" | "projects", boolean>>>;
    explorer_section_order?: Record<string, string[]>;
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

const readSettings = (): IUserSettings => {
    try {
        const settings = JSON.parse(localStorage.getItem(USER_SETTINGS_STORE_STORAGE_KEY) || "{}");
        return settings && typeof settings === "object" && !Array.isArray(settings) ? settings : {};
    } catch {
        // Browser preferences must not prevent app initialization. Leave the stored value intact.
        return {};
    }
};

const useUserSettingsStore = create(
    immer<IUserSettingsStore>((set, get) => {
        const updateStorage = () => {
            setTimeout(() => {
                try {
                    localStorage.setItem(USER_SETTINGS_STORE_STORAGE_KEY, JSON.stringify(get().settings));
                } catch {
                    // Keep this session usable when browser storage is unavailable or full.
                }
            }, 0);
        };

        return {
            settings: readSettings(),
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

export const useUserSettings = <TKey extends keyof IUserSettings>(key: TKey): IUserSettings[TKey] =>
    useUserSettingsStore((state) => state.settings[key]);

export const useWorkbenchContextOpen = (userUID?: string) => {
    const saved = useUserSettings("workbench_context_open");
    const open = userUID ? (saved?.[userUID] ?? true) : true;
    const setOpen = useCallback(
        (value: boolean | ((previous: boolean) => boolean)) => {
            if (!userUID) return;
            const store = getUserSettingsStore();
            const current = store.settings.workbench_context_open ?? {};
            const previous = current[userUID] ?? true;
            const next = typeof value === "function" ? value(previous) : value;
            store.updateSettingsByKey("workbench_context_open", { ...current, [userUID]: next });
            if (next && store.settings.workbench_sidebar_collapsed?.[userUID]) {
                store.updateSettingsByKey("workbench_sidebar_collapsed", { ...store.settings.workbench_sidebar_collapsed, [userUID]: false });
            }
        },
        [userUID]
    );
    return [open, setOpen] as const;
};

export default useUserSettingsStore;

export function normalizePinnedApps(value: unknown): IPinnedApp[] {
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
        pins.push({ key: item.key, name: Array.from(item.name.trim()).slice(0, 80).join("") });
        seen.add(item.key);
        if (pins.length === MAX_PINNED_APPS) break;
    }
    return pins;
}

export function usePinnedApps(userUID?: string) {
    const saved = useUserSettings("pinned_apps");
    const pins = useMemo(() => normalizePinnedApps(userUID ? saved?.[userUID] : undefined), [saved, userUID]);
    const replace = useCallback(
        (next: IPinnedApp[]) => {
            if (!userUID) return;
            const store = getUserSettingsStore();
            const current = store.settings.pinned_apps ?? {};
            store.updateSettingsByKey("pinned_apps", {
                ...current,
                [userUID]: normalizePinnedApps(next),
            });
        },
        [userUID]
    );
    return { pins, replace };
}
