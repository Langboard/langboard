import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useQuery } from "@tanstack/react-query";

export const reminderBrowserPushSettingsPath = "/notification/settings/web-push";

export interface ReminderBrowserPushPolicy {
    quiet_hours: { timezone: string; start_minute: number; end_minute: number } | null;
    block_urgent: boolean;
    show_preview: boolean;
}

interface PushSettings {
    available: boolean;
    application_server_key: string | null;
    subscribed: boolean;
    policy: ReminderBrowserPushPolicy;
}

export default function useReminderBrowserPushSettings(enabled: boolean) {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const queryKey = ["reminder-browser-push", actorUID, session] as const;
    const settings = useQuery({
        queryKey,
        enabled: !!actorUID && enabled,
        retry: false,
        staleTime: 30_000,
        queryFn: async ({ signal }) => (await api.get<PushSettings>(reminderBrowserPushSettingsPath, { signal })).data,
    });
    return { settings, queryKey, actorUID, session };
}
