import { useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Dialog from "@/components/base/Dialog";
import Table from "@/components/base/Table";
import Toast from "@/components/base/Toast";
import useGetWebhookEvents, { IWebhookEventOption } from "@/controllers/api/settings/webhooks/useGetWebhookEvents";
import useUpdateWebhook from "@/controllers/api/settings/webhooks/useUpdateWebhook";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { ROUTES } from "@/core/routing/constants";
import WebhookEventSelector from "@/pages/SettingsPage/components/webhook/WebhookEventSelector";
import { EHttpStatus } from "@langboard/core/enums";

/** Displays and edits the source-side event allowlist for one webhook endpoint. */
function WebhookEvents(): React.JSX.Element {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { model: webhook } = ModelRegistry.WebhookModel.useContext();
    const currentEvents = webhook.useField("events") ?? null;
    const currentEnabled = webhook.useField("is_enabled") !== false;
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const ownerApps = webhook.useField("owner_apps") ?? [];
    const canUpdateWebhook = !ownerApps.length && hasRoleAction(SettingRole.EAction.WebhookUpdate);
    const { mutateAsync, isPending } = useUpdateWebhook(webhook, { interceptToast: true });
    const { mutate: getWebhookEvents } = useGetWebhookEvents({ interceptToast: true });
    const [opened, setOpened] = useState(false);
    const [enabled, setEnabled] = useState(currentEnabled);
    const [initial, setInitial] = useState({ enabled: currentEnabled, events: currentEvents });
    const [allEvents, setAllEvents] = useState(currentEvents === null);
    const [selectedEvents, setSelectedEvents] = useState<string[]>(currentEvents ?? []);
    const [eventOptions, setEventOptions] = useState<IWebhookEventOption[]>([]);
    const [error, setError] = useState<string>();

    const changeOpenedState = (nextOpened: bool) => {
        if (isPending) {
            return;
        }

        setOpened(nextOpened);
        if (!nextOpened) {
            return;
        }

        setInitial({ enabled: currentEnabled, events: currentEvents });
        setEnabled(currentEnabled);
        setAllEvents(currentEvents === null);
        setSelectedEvents(currentEvents ?? []);
        setError(undefined);
        if (!eventOptions.length) {
            getWebhookEvents(
                {},
                {
                    onSuccess: setEventOptions,
                }
            );
        }
    };

    const save = () => {
        if (!allEvents && selectedEvents.length === 0) {
            setError("settings.Select at least one webhook event.");
            return;
        }

        const events = allEvents ? null : selectedEvents;
        const changes = {
            ...(JSON.stringify(initial.events) !== JSON.stringify(events) ? { events } : {}),
            ...(enabled !== initial.enabled ? { is_enabled: enabled } : {}),
        };
        if (Object.keys(changes).length === 0) {
            setOpened(false);
            return;
        }
        const promise = mutateAsync(changes);
        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (requestError) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler(
                    {
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                        },
                    },
                    messageRef
                );
                handle(requestError);
                return messageRef.message;
            },
            success: () => {
                setOpened(false);
                return t("settings.Webhook settings saved");
            },
        });
    };

    return (
        <Table.FlexCell className="w-1/6 truncate text-center">
            <Button type="button" variant="ghost" disabled={!canUpdateWebhook} onClick={() => changeOpenedState(true)}>
                {!currentEnabled
                    ? t("common.Disabled")
                    : currentEvents === null
                      ? t("settings.All events")
                      : t("settings.Selected event count", { count: currentEvents.length })}
            </Button>
            <Dialog.Root open={opened} onOpenChange={changeOpenedState}>
                <Dialog.Content className="sm:max-w-lg" aria-describedby="">
                    <Dialog.Header>
                        <Dialog.Title>{t("settings.Webhook events")}</Dialog.Title>
                    </Dialog.Header>
                    <Checkbox
                        checked={enabled}
                        onCheckedChange={(checked) => setEnabled(checked === true)}
                        label={t("common.Enabled")}
                        disabled={isPending}
                    />
                    <WebhookEventSelector
                        allEvents={allEvents}
                        setAllEvents={setAllEvents}
                        selectedEvents={selectedEvents}
                        setSelectedEvents={setSelectedEvents}
                        options={eventOptions}
                        disabled={isPending}
                        error={error ? t(error) : undefined}
                    />
                    <Dialog.Footer className="mt-6">
                        <Button type="button" variant="secondary" disabled={isPending} onClick={() => changeOpenedState(false)}>
                            {t("common.Cancel")}
                        </Button>
                        <Button type="button" disabled={isPending} onClick={save}>
                            {t("common.Save")}
                        </Button>
                    </Dialog.Footer>
                </Dialog.Content>
            </Dialog.Root>
        </Table.FlexCell>
    );
}

export default WebhookEvents;
