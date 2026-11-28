import Button from "@/components/base/Button";
import DateDistance from "@/components/DateDistance";
import CardActivitySettings from "@/components/NotificationSetting/CardActivitySettings";
import ReminderSnoozeMenu from "@/components/NotificationSetting/ReminderSnoozeMenu";
import { getNotificationRoute } from "@/controllers/api/notification/getNotificationRoute";
import { INotificationInboxThread, useInboxActivity, useSnoozeReminder } from "@/controllers/api/notification/useNotificationInbox";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

export default function NotificationActivity({ thread }: { thread: INotificationInboxThread }) {
    const [t] = useTranslation();
    const eventUIDs = [...new Set([thread.latest_event_uid, ...thread.observed_state_event_uids])];
    const activity = useInboxActivity(eventUIDs);
    const events = activity.data?.pages.flat();
    const snooze = useSnoozeReminder();
    return (
        <section aria-label={t("notification.inbox.activity")} className="space-y-2 border-t bg-muted/40 p-3 text-sm">
            {thread.project_uid && thread.card_uid && (
                <CardActivitySettings key={thread.card_uid} projectUID={thread.project_uid} cardUID={thread.card_uid} showReasons />
            )}
            {activity.isPending && <p role="status">{t("notification.inbox.loading")}</p>}
            {activity.isError && (
                <div role="alert" className="flex items-center gap-2">
                    {t("notification.inbox.loadError")}
                    <Button variant="outline" size="sm" onClick={() => void activity.refetch()}>
                        {t("notification.inbox.retry")}
                    </Button>
                </div>
            )}
            {snooze.isError && <p role="alert">{t("notification.inbox.snoozeError")}</p>}
            {!!events?.length && (
                <ul className="space-y-2">
                    {events.map((event) => {
                        const route = getNotificationRoute(event);
                        const reason = t(`notification.inbox.reason.${event.type}`);
                        const actor = event.notifier_user
                            ? [event.notifier_user.firstname, event.notifier_user.lastname].filter(Boolean).join(" ")
                            : event.notifier_bot?.name;
                        const excerpt = event.message_vars.line;
                        return (
                            <li key={event.uid} className="rounded-md border bg-background px-3 py-2">
                                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                    {route ? (
                                        <Link className="font-medium hover:underline" to={route}>
                                            {reason}
                                        </Link>
                                    ) : (
                                        <span className="font-medium">{reason}</span>
                                    )}
                                    {actor && <span className="text-muted-foreground">{actor}</span>}
                                    <DateDistance date={new Date(event.created_at)} className="text-xs text-muted-foreground" />
                                    {event.can_snooze && (
                                        <ReminderSnoozeMenu
                                            className="ml-auto gap-1.5"
                                            disabled={snooze.isPending}
                                            onSnooze={(minutes) => snooze.mutate({ uid: event.uid, minutes })}
                                        />
                                    )}
                                </div>
                                {typeof excerpt === "string" && excerpt && <p className="mt-1 break-words text-muted-foreground">{excerpt}</p>}
                            </li>
                        );
                    })}
                </ul>
            )}
            {events?.length === 0 && !activity.isError && <p>{t("notification.No notifications received.")}</p>}
            {activity.hasNextPage && (
                <Button variant="outline" size="sm" disabled={activity.isFetching} onClick={() => void activity.fetchNextPage()}>
                    {t("notification.inbox.more")}
                </Button>
            )}
        </section>
    );
}
