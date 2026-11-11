import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Popover from "@/components/base/Popover";
import Toast from "@/components/base/Toast";
import NotificationSettingChannelSwitch from "@/components/NotificationSetting/Switch";
import { TSpecificScopedPopoverProps } from "@/components/NotificationSetting/types";
import { DISABLE_DRAGGING_ATTR } from "@/constants";
import useToggleSpecificScopedNotificationSettings from "@/controllers/api/notification/settings/useToggleSpecificScopedNotificationSettings";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { AuthUser } from "@/core/models";
import { ENotificationChannel, ENotificationScope } from "@/core/models/types/notification.type";
import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";

const SpecificScopedPopover = memo(
    ({
        modal,
        type,
        form,
        currentUser,
        specificUID,
        triggerProps,
        iconProps,
        showTriggerText,
        onlyFlex,
        onlyPopover,
    }: TSpecificScopedPopoverProps) => {
        const [t] = useTranslation();
        const { mutateAsync } = useToggleSpecificScopedNotificationSettings(type, currentUser, { interceptToast: true });
        const unsubscriptions = currentUser.useField("notification_unsubs");
        const subscribedChannelMap = useMemo(
            () => ({
                [ENotificationChannel.Web]: hasSubscription(unsubscriptions, ENotificationChannel.Web, specificUID),
                [ENotificationChannel.Email]: hasSubscription(unsubscriptions, ENotificationChannel.Email, specificUID),
                [ENotificationChannel.Mobile]: hasSubscription(unsubscriptions, ENotificationChannel.Mobile, specificUID),
                [ENotificationChannel.IoT]: hasSubscription(unsubscriptions, ENotificationChannel.IoT, specificUID),
            }),
            [unsubscriptions, specificUID]
        );

        const toggle = (channel: ENotificationChannel, endCallback: () => void) => {
            const promise = mutateAsync({
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                ...(form as any),
                channel,
                is_unsubscribed: subscribedChannelMap[channel],
            });

            Toast.Add.promise(promise, {
                loading: t("common.Updating..."),
                error: (error) => {
                    const messageRef = { message: "" };
                    const { handle } = setupApiErrorHandler({}, messageRef);

                    handle(error);
                    return messageRef.message;
                },
                success: () => {
                    return t("successes.Notification settings updated successfully.");
                },
                finally: () => {
                    endCallback();
                },
            });
        };

        return (
            <>
                <Box items="center" gap="2" wrap display={onlyFlex ? "flex" : onlyPopover ? "hidden" : { initial: "hidden", sm: "flex" }}>
                    {Object.keys(ENotificationChannel).map((channelKey) => (
                        <NotificationSettingChannelSwitch
                            key={`${currentUser.uid}:${specificUID}:${channelKey}`}
                            channel={ENotificationChannel[channelKey]}
                            toggle={toggle}
                            hasSubscription={subscribedChannelMap[ENotificationChannel[channelKey]]}
                        />
                    ))}
                </Box>
                <Box display={onlyFlex ? "hidden" : onlyPopover ? "block" : { sm: "hidden" }}>
                    <Popover.Root modal={modal}>
                        <Popover.Trigger asChild>
                            <Button {...triggerProps}>
                                <IconComponent icon="bell-ring" {...iconProps} />
                                {showTriggerText && t("notification.settings.Notification settings")}
                            </Button>
                        </Popover.Trigger>
                        <Popover.Content className="w-auto p-3" {...{ [DISABLE_DRAGGING_ATTR]: "" }}>
                            <Flex direction="col" gap="2.5">
                                {Object.keys(ENotificationChannel).map((channelKey) => (
                                    <NotificationSettingChannelSwitch
                                        key={`${currentUser.uid}:${specificUID}:${channelKey}`}
                                        channel={ENotificationChannel[channelKey]}
                                        toggle={toggle}
                                        hasSubscription={subscribedChannelMap[ENotificationChannel[channelKey]]}
                                    />
                                ))}
                            </Flex>
                        </Popover.Content>
                    </Popover.Root>
                </Box>
            </>
        );
    }
);

const hasSubscription = (unsubscriptions: AuthUser.TModel["notification_unsubs"], channel: ENotificationChannel, specificUID: string) => {
    return !Object.values(unsubscriptions?.[ENotificationScope.Specific] ?? {})
        .map((notificationTypes) => notificationTypes?.[channel] ?? [])
        .flat()
        .includes(specificUID);
};

export default SpecificScopedPopover;
