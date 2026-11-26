import Button from "@/components/base/Button";
import ReminderBrowserPushPolicyDialog from "./ReminderBrowserPushPolicyDialog";
import { canUseReminderPush, subscribeReminderPush, unsubscribeReminderPush } from "@/controllers/api/notification/reminderBrowserPush";
import useReminderBrowserPushSettings, {
    reminderBrowserPushSettingsPath as path,
} from "@/controllers/api/notification/useReminderBrowserPushSettings";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

export default function ReminderBrowserPushToggle() {
    const [t] = useTranslation();
    const { actorUID, session, queryKey, settings } = useReminderBrowserPushSettings(canUseReminderPush());
    const queryClient = useQueryClient();
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
            {current.policy && (
                <ReminderBrowserPushPolicyDialog policy={current.policy} onSaved={() => queryClient.invalidateQueries({ queryKey, exact: true })} />
            )}
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
