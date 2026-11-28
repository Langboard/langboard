import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import Label from "@/components/base/Label";
import Popover from "@/components/base/Popover";
import ScrollArea from "@/components/base/ScrollArea";
import Switch from "@/components/base/Switch";
import Toast from "@/components/base/Toast";
import DateDistance from "@/components/DateDistance";
import ReminderSnoozeMenu from "@/components/NotificationSetting/ReminderSnoozeMenu";
import { getNotificationRoute } from "@/controllers/api/notification/getNotificationRoute";
import useNotificationCommand from "@/controllers/api/notification/useNotificationCommand";
import {
    getInboxActivity,
    getInboxRows,
    INotificationInboxThread,
    notificationInboxKey,
    notificationThreadKey,
    useNotificationInbox,
    useObservedInboxCommand,
    useSnoozeReminder,
} from "@/controllers/api/notification/useNotificationInbox";
import useUserNotificationMutatedHandlers from "@/controllers/socket/user/useUserNotificationMutatedHandlers";
import useUserNotifiedHandlers from "@/controllers/socket/user/useUserNotifiedHandlers";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useSwitchSocketHandlers from "@/core/hooks/useSwitchSocketHandlers";
import { AuthUser } from "@/core/models";
import { useSocket } from "@/core/providers/SocketProvider";
import { ROUTES } from "@/core/routing/constants";
import useAuthStore from "@/core/stores/AuthStore";
import { useQueryClient } from "@tanstack/react-query";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNotificationNavigation } from "./useNotificationNavigation";

interface IHeaderUserNotificationProps {
    currentUser: AuthUser.TModel;
}

const HeaderUserNotification = memo(({ currentUser }: IHeaderUserNotificationProps) => {
    const [t] = useTranslation();
    const socket = useSocket();
    const queryClient = useQueryClient();
    const [unreadOnly, setUnreadOnly] = useState(true);
    const [isOpened, setIsOpened] = useState(false);
    const [opening, setOpening] = useState(false);
    const [openError, setOpenError] = useState(false);
    const pendingNotice = useRef<string | undefined>(undefined);
    const inbox = useNotificationInbox({ unread_only: unreadOnly });
    const command = useObservedInboxCommand();
    const snooze = useSnoozeReminder();
    const deleteCommand = useNotificationCommand();
    const unreadCount = inbox.data?.pages[0]?.unread_count ?? 0;
    const rows = getInboxRows(inbox.data?.pages);
    const visibleUnread = [...new Set(rows.flatMap((row) => row.observed_unread_event_uids))];
    const busy = opening || command.isPending || deleteCommand.isPending || snooze.isPending;
    const refresh = useCallback(() => {
        void queryClient.invalidateQueries({ queryKey: notificationInboxKey(currentUser.uid) });
    }, [currentUser.uid, queryClient]);

    const handlers = useMemo(
        () => [
            useUserNotifiedHandlers({
                currentUser,
                callback: ({ notification }) => {
                    if (typeof notification?.uid === "string") pendingNotice.current = notification.uid;
                    refresh();
                },
            }),
            useUserNotificationMutatedHandlers({ currentUser, callback: refresh }),
        ],
        [currentUser, refresh]
    );
    useSwitchSocketHandlers({ socket, handlers });
    useEffect(() => {
        const uid = pendingNotice.current;
        if (uid && inbox.data?.pages.some((page) => page.threads.some((row) => row.observed_unread_event_uids.includes(uid)))) {
            pendingNotice.current = undefined;
            if (!isOpened) Toast.Add.info(t("notification.You have a new notification."));
        }
    }, [inbox.data, isOpened, t]);
    useEffect(() => {
        const eventKey = `user-notification-refresh-${currentUser.uid}`;
        socket.on({ event: "open", eventKey, callback: refresh });
        return () => socket.off({ event: "open", eventKey, callback: refresh });
    }, [currentUser.uid, refresh, socket]);

    const closeNotifications = useCallback(() => setIsOpened(false), []);
    const navigate = usePageNavigateRef();
    const { closeThenNavigate, onCloseAutoFocus } = useNotificationNavigation(closeNotifications, navigate);
    const openRow = async (row: INotificationInboxThread) => {
        setOpening(true);
        setOpenError(false);
        const identityVersion = useAuthStore.getState().getIdentityVersion();
        try {
            const route =
                row.project_uid && row.card_uid
                    ? ROUTES.BOARD.CARD(row.project_uid, row.card_uid)
                    : (await getInboxActivity([row.latest_event_uid])).map(getNotificationRoute)[0];
            if (identityVersion !== useAuthStore.getState().getIdentityVersion()) return;
            if (!route) {
                setOpenError(true);
                return;
            }
            if (row.observed_unread_event_uids.length) {
                await command.mutateAsync({ action: "read", event_uids: row.observed_unread_event_uids });
            }
            if (identityVersion === useAuthStore.getState().getIdentityVersion()) closeThenNavigate(route);
        } catch {
            setOpenError(true);
        } finally {
            setOpening(false);
        }
    };

    return (
        <Popover.Root
            modal
            open={isOpened}
            onOpenChange={(open) => {
                setIsOpened(open);
                setOpenError(false);
                command.reset();
                snooze.reset();
            }}
        >
            <Popover.Trigger asChild>
                <Button
                    variant="ghost"
                    className="relative p-2"
                    data-notification-surface="trigger"
                    aria-label={t("notification.Notifications")}
                    title={t(unreadCount ? "notification.{count} notifications received" : "notification.Notifications", { count: unreadCount })}
                >
                    <IconComponent icon="bell" />
                    {unreadCount > 0 && (
                        <Box
                            position="absolute"
                            top="0"
                            right="0"
                            px="0.5"
                            rounded="sm"
                            textSize="xs"
                            className="bg-destructive text-destructive-foreground"
                        >
                            {unreadCount > 99 ? "99+" : unreadCount}
                        </Box>
                    )}
                </Button>
            </Popover.Trigger>
            <Popover.Content
                data-notification-surface="content"
                onCloseAutoFocus={onCloseAutoFocus}
                className="min-w-[min(theme(screens.xs),100vw)] max-w-[min(theme(screens.xs),100vw)] p-0"
            >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2.5">
                    <span className="whitespace-nowrap font-semibold">{t("notification.Notifications")}</span>
                    <div className="ml-auto flex items-center gap-1.5">
                        <Label display="inline-flex" cursor="pointer" items="center" gap="2" textSize="xs">
                            <Switch checked={unreadOnly} onCheckedChange={setUnreadOnly} />
                            <span>{t("notification.Only show unread")}</span>
                        </Label>
                        {visibleUnread.length > 0 && (
                            <Button
                                variant="ghost"
                                size="icon-sm"
                                disabled={busy}
                                title={t("notification.inbox.readVisible")}
                                onClick={() => command.mutate({ action: "read", event_uids: visibleUnread })}
                            >
                                <IconComponent icon="check-check" size="4" />
                            </Button>
                        )}
                    </div>
                </div>
                {(openError || command.isError) && (
                    <p role="alert" className="px-3 py-2 text-sm text-destructive">
                        {t("notification.inbox.changeError")}
                    </p>
                )}
                {snooze.isError && (
                    <p role="alert" className="px-3 py-2 text-sm text-destructive">
                        {t("notification.inbox.snoozeError")}
                    </p>
                )}
                <ScrollArea.Root className="h-[min(theme(spacing.96),calc(100vh_-_theme(spacing.16)_-_theme(spacing.28)))]" mutable={rows.length}>
                    {inbox.isPending && (
                        <p role="status" className="p-3 text-sm">
                            {t("notification.inbox.loading")}
                        </p>
                    )}
                    {inbox.isError && (
                        <div role="alert" className="flex items-center gap-2 p-3 text-sm">
                            {t("notification.inbox.loadError")}
                            <Button size="sm" variant="outline" onClick={() => void inbox.refetch()}>
                                {t("notification.inbox.retry")}
                            </Button>
                        </div>
                    )}
                    {!inbox.isPending && !inbox.isError && rows.length === 0 && (
                        <p className="p-6 text-center text-sm">{t("notification.No notifications received.")}</p>
                    )}
                    <ul className="divide-y">
                        {rows.map((row) => {
                            const reason = t(`notification.inbox.reason.${row.latest_event_type}`);
                            const reminderUID = row.snoozable_reminder_event_uid;
                            return (
                                <li key={notificationThreadKey(row)} className="flex items-center gap-2 px-3 py-2">
                                    <Button
                                        variant="ghost"
                                        disabled={busy}
                                        className="h-auto min-w-0 flex-1 justify-start whitespace-normal p-1 text-left"
                                        onClick={() => void openRow(row)}
                                    >
                                        <span className="min-w-0">
                                            <span className="block break-words font-semibold">{row.card_title ?? reason}</span>
                                            <span className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                                                {row.card_title && <span>{reason}</span>}
                                                {row.unread_mention && (
                                                    <span className="font-semibold text-primary">{t("notification.inbox.mention")}</span>
                                                )}
                                                {row.unread_events > 0 && (
                                                    <span>{t("notification.inbox.unreadCount", { count: row.unread_events })}</span>
                                                )}
                                            </span>
                                        </span>
                                    </Button>
                                    <DateDistance date={new Date(row.latest_at)} className="shrink-0 text-xs text-muted-foreground" />
                                    {reminderUID && (
                                        <ReminderSnoozeMenu
                                            className="shrink-0 gap-1"
                                            disabled={busy}
                                            onSnooze={(minutes) => snooze.mutate({ uid: reminderUID, minutes })}
                                        />
                                    )}
                                    {row.unread_events > 0 && (
                                        <Button
                                            variant="ghost"
                                            size="icon-sm"
                                            disabled={busy}
                                            title={t("notification.Read notification")}
                                            onClick={() => command.mutate({ action: "read", event_uids: row.observed_unread_event_uids })}
                                        >
                                            <IconComponent icon="check" size="4" />
                                        </Button>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                    {inbox.hasNextPage && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="m-2"
                            disabled={inbox.isFetching || busy}
                            onClick={() => void inbox.fetchNextPage()}
                        >
                            {t("notification.inbox.more")}
                        </Button>
                    )}
                </ScrollArea.Root>
                <div className="flex items-center gap-2 border-t p-2">
                    <Button variant="ghost" className="flex-1" onClick={() => closeThenNavigate(ROUTES.NOTIFICATION_CENTER)}>
                        {t("notification.inbox.open")}
                    </Button>
                    {rows.length > 0 && (
                        <Button
                            variant="destructive-ghost"
                            size="icon-sm"
                            disabled={busy}
                            title={t("notification.Delete all notifications")}
                            onClick={() => deleteCommand.mutate({ action: "delete_all" }, { onSuccess: refresh })}
                        >
                            <IconComponent icon="trash-2" size="4" />
                        </Button>
                    )}
                </div>
            </Popover.Content>
        </Popover.Root>
    );
});

export default HeaderUserNotification;
