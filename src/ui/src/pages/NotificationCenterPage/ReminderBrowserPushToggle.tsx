import Button from "@/components/base/Button";
import { canUseReminderPush, subscribeReminderPush, unsubscribeReminderPush } from "@/controllers/api/notification/reminderBrowserPush";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

const path = "/notification/settings/web-push";
interface PushSettings {
    available: boolean;
    application_server_key: string | null;
    subscribed: boolean;
}

export default function ReminderBrowserPushToggle() {
    const [t] = useTranslation();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const queryClient = useQueryClient();
    const queryKey = ["reminder-browser-push", actorUID, session] as const;
    const settings = useQuery({
        queryKey,
        enabled: !!actorUID && canUseReminderPush(),
        retry: false,
        queryFn: async ({ signal }) => (await api.get<PushSettings>(path, { signal })).data,
    });
    const change = useMutation({
        retry: false,
        mutationFn: async (key: string | null) => {
            const assertSession = () => {
                const auth = useAuthStore.getState();
                if (!actorUID || auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session) {
                    throw new Error("Browser push session changed");
                }
            };
            assertSession();
            if (key) {
                const subscription = (await subscribeReminderPush(key)).toJSON();
                assertSession();
                await api.put(path, { endpoint: subscription.endpoint, keys: subscription.keys });
            } else {
                await api.delete(path);
                assertSession();
                await unsubscribeReminderPush();
            }
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey, exact: true }),
    });
    const current = settings.data;
    if (!current?.available || !current.application_server_key) return null;

    return (
        <div className="flex flex-wrap items-center gap-2">
            <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={change.isPending}
                onClick={() => change.mutate(current.subscribed ? null : current.application_server_key)}
            >
                {t(current.subscribed ? "notification.webPush.disable" : "notification.webPush.enable")}
            </Button>
            {change.isError && (
                <span role="alert" className="text-sm text-muted-foreground">
                    {t(
                        change.error instanceof DOMException && change.error.name === "NotAllowedError"
                            ? "notification.webPush.permission"
                            : "notification.webPush.error"
                    )}
                </span>
            )}
        </div>
    );
}
