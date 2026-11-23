import Button from "@/components/base/Button";
import NotificationActivity from "./NotificationActivity";
import DateDistance from "@/components/DateDistance";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import {
    getInboxRows,
    INotificationInboxThread,
    notificationThreadKey,
    TNotificationInboxState,
    useNotificationInbox,
    useObservedInboxCommand,
} from "@/controllers/api/notification/useNotificationInbox";
import { ENotificationType } from "@/core/models/types/notification.type";
import { ROUTES } from "@/core/routing/constants";
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

const inboxStates: TNotificationInboxState[] = ["inbox", "later", "done"];

export default function NotificationCenterPage() {
    const [t] = useTranslation();
    const [expanded, setExpanded] = useState<string>();
    const [state, setState] = useState<TNotificationInboxState>("inbox");
    const [unreadOnly, setUnreadOnly] = useState(false);
    const [projectUID, setProjectUID] = useState("");
    const [reason, setReason] = useState<ENotificationType>();
    const [selected, setSelected] = useState<Map<string, INotificationInboxThread>>(new Map());
    const { data: projects } = useGetProjects();
    const inbox = useNotificationInbox({ state, unread_only: unreadOnly, project_uid: projectUID || undefined, reason });
    const command = useObservedInboxCommand();
    const threads = getInboxRows(inbox.data?.pages);
    const chosen = [...selected.values()];
    const availableStates = inboxStates.filter((value) => value !== state);
    const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

    const resetSelection = () => {
        setSelected(new Map());
        setExpanded(undefined);
        command.reset();
    };

    const apply = async (rows: INotificationInboxThread[], target: "read" | TNotificationInboxState) => {
        const event_uids = [...new Set(rows.flatMap((row) => (target === "read" ? row.observed_unread_event_uids : row.observed_state_event_uids)))];
        if (!event_uids.length) {
            return;
        }
        try {
            await command.mutateAsync(
                target === "read" ? { action: "read", event_uids } : { action: "state", event_uids, expected_state: state, state: target }
            );
            setSelected((previous) => {
                const next = new Map(previous);
                rows.forEach((row) => next.delete(notificationThreadKey(row)));
                return next;
            });
        } catch {
            // Keep the selection so the user can inspect the refreshed result.
        }
    };

    return (
        <main className="mx-auto flex h-full min-h-0 w-full max-w-5xl flex-col gap-4 overflow-auto px-4 py-5 sm:px-6">
            <h1 className="text-2xl font-semibold tracking-tight">{t("notification.inbox.title")}</h1>
            <div className="flex flex-wrap items-center gap-2">
                <div role="group" aria-label={t("notification.inbox.title")} className="flex flex-wrap gap-1">
                    {inboxStates.map((value) => (
                        <Button
                            key={value}
                            size="sm"
                            variant={state === value ? "secondary" : "ghost"}
                            aria-pressed={state === value}
                            disabled={command.isPending}
                            onClick={() => {
                                resetSelection();
                                setState(value);
                            }}
                        >
                            {t(`notification.inbox.${value}`)}
                        </Button>
                    ))}
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-sm">
                    <input
                        type="checkbox"
                        checked={unreadOnly}
                        disabled={command.isPending}
                        onChange={(event) => {
                            resetSelection();
                            setUnreadOnly(event.currentTarget.checked);
                        }}
                    />
                    {t("notification.Only show unread")}
                </label>
                <select
                    className={selectClass}
                    value={projectUID}
                    aria-label={t("notification.inbox.boards")}
                    disabled={command.isPending}
                    onChange={(event) => {
                        resetSelection();
                        setProjectUID(event.currentTarget.value);
                    }}
                >
                    <option value="">{t("notification.inbox.boards")}</option>
                    {projects?.projects.map((project) => (
                        <option key={project.uid} value={project.uid}>
                            {project.title}
                        </option>
                    ))}
                </select>
                <select
                    className={selectClass}
                    value={reason ?? ""}
                    aria-label={t("notification.inbox.reasons")}
                    disabled={command.isPending}
                    onChange={(event) => {
                        resetSelection();
                        setReason((event.currentTarget.value as ENotificationType) || undefined);
                    }}
                >
                    <option value="">{t("notification.inbox.reasons")}</option>
                    {Object.values(ENotificationType).map((value) => (
                        <option key={value} value={value}>
                            {t(`notification.inbox.reason.${value}`)}
                        </option>
                    ))}
                </select>
            </div>
            {chosen.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2" aria-label={t("notification.inbox.bulk")}>
                    <span className="text-sm tabular-nums">{chosen.length}</span>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={command.isPending || !chosen.some((row) => row.unread_events)}
                        onClick={() => void apply(chosen, "read")}
                    >
                        {t("notification.Read notification")}
                    </Button>
                    {availableStates.map((value) => (
                        <Button key={value} size="sm" variant="outline" disabled={command.isPending} onClick={() => void apply(chosen, value)}>
                            {t(`notification.inbox.action.${value}`)}
                        </Button>
                    ))}
                </div>
            )}
            {command.isError && (
                <p role="alert" className="text-sm text-destructive">
                    {t("notification.inbox.changeError")}
                </p>
            )}
            {inbox.isPending && (
                <p role="status" className="text-sm text-muted-foreground">
                    {t("notification.inbox.loading")}
                </p>
            )}
            {inbox.isError && (
                <div role="alert" className="flex items-center gap-2 text-sm text-destructive">
                    {t("notification.inbox.loadError")}
                    <Button size="sm" variant="outline" onClick={() => void inbox.refetch()}>
                        {t("notification.inbox.retry")}
                    </Button>
                </div>
            )}
            {!inbox.isPending && !inbox.isError && threads.length === 0 && (
                <p className="rounded-lg border p-6 text-sm text-muted-foreground">{t("notification.No notifications received.")}</p>
            )}
            {threads.length > 0 && (
                <ul className="divide-y rounded-lg border">
                    {threads.map((thread) => {
                        const key = notificationThreadKey(thread);
                        const description = t(`notification.inbox.reason.${thread.latest_event_type}`);
                        const title = thread.card_title ?? description;
                        const route = thread.project_uid && thread.card_uid ? ROUTES.BOARD.CARD(thread.project_uid, thread.card_uid) : undefined;
                        return (
                            <li key={key}>
                                <div className="flex items-start gap-3 px-3 py-3 sm:items-center">
                                    <input
                                        type="checkbox"
                                        checked={selected.has(key)}
                                        disabled={command.isPending}
                                        aria-label={title}
                                        onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                                            const checked = event.currentTarget.checked;
                                            setSelected((previous) => {
                                                const next = new Map(previous);
                                                if (checked) next.set(key, thread);
                                                else next.delete(key);
                                                return next;
                                            });
                                        }}
                                    />
                                    <div className="min-w-0 flex-1">
                                        {route ? (
                                            <Link className="break-words text-sm font-semibold hover:underline" to={route}>
                                                {title}
                                            </Link>
                                        ) : (
                                            <p className="break-words text-sm font-semibold">{title}</p>
                                        )}
                                        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                                            {thread.card_title && <span>{description}</span>}
                                            {thread.unread_mention && (
                                                <span className="font-semibold text-primary">{t("notification.inbox.mention")}</span>
                                            )}
                                            {thread.unread_events > 0 && (
                                                <span>{t("notification.inbox.unreadCount", { count: thread.unread_events })}</span>
                                            )}
                                            <DateDistance date={new Date(thread.latest_at)} />
                                        </div>
                                    </div>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        aria-expanded={expanded === key}
                                        onClick={() => setExpanded((current) => (current === key ? undefined : key))}
                                    >
                                        {t("notification.inbox.activity")}
                                    </Button>
                                    {thread.unread_events > 0 && (
                                        <Button size="sm" variant="ghost" disabled={command.isPending} onClick={() => void apply([thread], "read")}>
                                            {t("notification.Read notification")}
                                        </Button>
                                    )}
                                </div>
                                {expanded === key && <NotificationActivity thread={thread} />}
                            </li>
                        );
                    })}
                </ul>
            )}
            {inbox.hasNextPage && (
                <Button
                    size="sm"
                    variant="outline"
                    className="self-start"
                    disabled={inbox.isFetching || command.isPending}
                    onClick={() => void inbox.fetchNextPage()}
                >
                    {t("notification.inbox.more")}
                </Button>
            )}
        </main>
    );
}
