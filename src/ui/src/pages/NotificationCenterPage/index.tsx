import Button from "@/components/base/Button";
import IconComponent, { type TIconName } from "@/components/base/IconComponent";
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
import { cn } from "@/core/utils/ComponentUtils";
import { ENotificationType } from "@/core/models/types/notification.type";
import { ROUTES } from "@/core/routing/constants";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

const stateIcons = { inbox: "inbox", later: "bookmark", done: "check" } satisfies Record<TNotificationInboxState, TIconName>;
const inboxStates = Object.keys(stateIcons) as TNotificationInboxState[];

export default function NotificationCenterPage() {
    const [t, i18n] = useTranslation();
    const [expanded, setExpanded] = useState<string>();
    const [state, setState] = useState<TNotificationInboxState>("inbox");
    const [unreadOnly, setUnreadOnly] = useState(false);
    const [groupByBoard, setGroupByBoard] = useState(true);
    const [projectUID, setProjectUID] = useState("");
    const [reason, setReason] = useState<ENotificationType>();
    const [selected, setSelected] = useState<Map<string, INotificationInboxThread>>(new Map());
    const { data: projects } = useGetProjects();
    const inbox = useNotificationInbox({ state, unread_only: unreadOnly, project_uid: projectUID || undefined, reason });
    const command = useObservedInboxCommand();
    const threads = getInboxRows(inbox.data?.pages);
    const chosen = [...selected.values()];
    const availableStates = inboxStates.filter((value) => value !== state);
    const selectClass = "h-9 min-w-0 max-w-full rounded-md border bg-background px-2 text-sm";
    const allVisibleSelected = threads.length > 0 && threads.every((row) => selected.has(notificationThreadKey(row)));
    const boardTitles = new Map(projects?.projects.map((project) => [project.uid, project.title]));
    const groups = new Map<string | null, INotificationInboxThread[]>();
    for (const thread of threads) {
        const key = groupByBoard ? thread.project_uid : null;
        const group = groups.get(key);
        if (group) group.push(thread);
        else groups.set(key, [thread]);
    }

    const selectRows = (rows: INotificationInboxThread[], checked: boolean) => {
        setSelected((previous) => {
            const next = new Map(previous);
            for (const row of rows) {
                const key = notificationThreadKey(row);
                if (!checked) next.delete(key);
                else if (!next.has(key)) next.set(key, row);
            }
            return next;
        });
    };

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
        <main className="h-full min-h-0 w-full overflow-auto">
            <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
                <h1 className="mb-4 text-xl font-semibold tracking-tight">{t("notification.inbox.title")}</h1>
                <div className="grid min-w-0 gap-4 lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-6">
                    <nav aria-label={t("notification.inbox.title")} className="flex min-w-0 flex-wrap gap-1 lg:flex-col">
                        {inboxStates.map((value) => (
                            <Button
                                key={value}
                                size="sm"
                                variant={state === value ? "secondary" : "ghost"}
                                className="justify-start gap-2"
                                aria-pressed={state === value}
                                disabled={command.isPending}
                                onClick={() => {
                                    resetSelection();
                                    setState(value);
                                }}
                            >
                                <IconComponent icon={stateIcons[value]} className="size-4" aria-hidden />
                                {t(`notification.inbox.${value}`)}
                            </Button>
                        ))}
                    </nav>
                    <div className="min-w-0 space-y-3">
                        <div className="grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap">
                            <div role="group" aria-label={t("notification.Only show unread")} className="col-span-2 flex gap-1">
                                {[false, true].map((value) => (
                                    <Button
                                        key={String(value)}
                                        size="sm"
                                        variant={unreadOnly === value ? "secondary" : "ghost"}
                                        aria-pressed={unreadOnly === value}
                                        disabled={command.isPending}
                                        onClick={() => {
                                            resetSelection();
                                            setUnreadOnly(value);
                                        }}
                                    >
                                        {t(value ? "notification.inbox.unread" : "notification.timeRanges.all")}
                                    </Button>
                                ))}
                            </div>
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
                            <select
                                className={selectClass}
                                value={groupByBoard ? "board" : "none"}
                                aria-label={t("notification.inbox.groupBy")}
                                onChange={(event) => setGroupByBoard(event.currentTarget.value === "board")}
                            >
                                <option value="board">{t("notification.inbox.groupByBoard")}</option>
                                <option value="none">{t("notification.inbox.noGrouping")}</option>
                            </select>
                        </div>
                        {threads.length > 0 && (
                            <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/20 px-3 py-2">
                                <label className="flex min-h-7 items-center gap-2 text-xs font-medium">
                                    <input
                                        type="checkbox"
                                        className="size-4 accent-primary"
                                        checked={allVisibleSelected}
                                        aria-checked={allVisibleSelected ? true : chosen.length > 0 ? "mixed" : false}
                                        ref={(input) => {
                                            if (input) input.indeterminate = !allVisibleSelected && chosen.length > 0;
                                        }}
                                        disabled={command.isPending}
                                        onChange={(event) => selectRows(threads, event.currentTarget.checked)}
                                    />
                                    {t("notification.inbox.selectVisible")}
                                </label>
                                {chosen.length > 0 && (
                                    <div className="flex flex-wrap items-center gap-1" aria-label={t("notification.inbox.bulk")}>
                                        <span className="px-1 text-xs tabular-nums">{chosen.length}</span>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={command.isPending || !chosen.some((row) => row.unread_events)}
                                            onClick={() => void apply(chosen, "read")}
                                        >
                                            {t("notification.Read notification")}
                                        </Button>
                                        {availableStates.map((value) => (
                                            <Button
                                                key={value}
                                                size="sm"
                                                variant="ghost"
                                                disabled={command.isPending}
                                                onClick={() => void apply(chosen, value)}
                                            >
                                                {t(`notification.inbox.action.${value}`)}
                                            </Button>
                                        ))}
                                    </div>
                                )}
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
                        {[...groups].map(([boardUID, rows]) => {
                            const boardTitle =
                                rows.find((row) => row.project_title)?.project_title ??
                                (boardUID ? boardTitles.get(boardUID) : undefined) ??
                                t("notification.inbox.other");
                            const allSelected = rows.every((row) => selected.has(notificationThreadKey(row)));
                            const partiallySelected = !allSelected && rows.some((row) => selected.has(notificationThreadKey(row)));
                            return (
                                <section key={boardUID ?? "other"} className="min-w-0 overflow-hidden rounded-md border">
                                    {groupByBoard && (
                                        <header className="flex min-w-0 items-center gap-3 border-b bg-muted/35 px-3 py-2.5">
                                            <input
                                                type="checkbox"
                                                className="size-4 shrink-0 accent-primary"
                                                aria-label={t("notification.inbox.selectBoard", { board: boardTitle })}
                                                checked={allSelected}
                                                aria-checked={partiallySelected ? "mixed" : allSelected}
                                                ref={(input) => {
                                                    if (input) input.indeterminate = partiallySelected;
                                                }}
                                                disabled={command.isPending}
                                                onChange={(event) => selectRows(rows, event.currentTarget.checked)}
                                            />
                                            <h2 className="min-w-0 truncate text-sm font-semibold">
                                                {boardUID ? (
                                                    <Link className="hover:underline" to={ROUTES.BOARD.MAIN(boardUID)}>
                                                        {boardTitle}
                                                    </Link>
                                                ) : (
                                                    boardTitle
                                                )}
                                            </h2>
                                        </header>
                                    )}
                                    <ul className="divide-y">
                                        {rows.map((thread) => {
                                            const key = notificationThreadKey(thread);
                                            const description = t(`notification.inbox.reason.${thread.latest_event_type}`);
                                            const title = thread.card_title ?? description;
                                            const subscriptionReasons = thread.subscription_reasons?.map((reason) =>
                                                t(`notification.inbox.subscriptionReason.${reason}`)
                                            );
                                            const priority = thread.card_priority === "P2" ? undefined : thread.card_priority;
                                            const route =
                                                thread.project_uid && thread.card_uid
                                                    ? ROUTES.BOARD.CARD(thread.project_uid, thread.card_uid)
                                                    : undefined;
                                            return (
                                                <li key={key}>
                                                    <div
                                                        className={cn(
                                                            "grid grid-cols-[1rem_minmax(0,1fr)] items-start gap-x-3 gap-y-1",
                                                            "px-3 py-2.5 hover:bg-muted/20",
                                                            "sm:grid-cols-[1rem_minmax(0,1fr)_auto] sm:items-center"
                                                        )}
                                                    >
                                                        <input
                                                            type="checkbox"
                                                            className="mt-1 size-4 accent-primary sm:mt-0"
                                                            checked={selected.has(key)}
                                                            disabled={command.isPending}
                                                            aria-label={title}
                                                            onChange={(event) => selectRows([thread], event.currentTarget.checked)}
                                                        />
                                                        <div className="min-w-0">
                                                            {route ? (
                                                                <Link
                                                                    className="break-words text-sm font-semibold underline-offset-4 hover:underline"
                                                                    to={route}
                                                                >
                                                                    {title}
                                                                </Link>
                                                            ) : (
                                                                <p className="break-words text-sm font-semibold">{title}</p>
                                                            )}
                                                            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                                                                {!groupByBoard && thread.project_title && <span>{thread.project_title}</span>}
                                                                {thread.card_column && <span>{thread.card_column}</span>}
                                                                {priority && (
                                                                    <span className="font-medium text-foreground">
                                                                        {t("notification.inbox.priority", { priority })}
                                                                    </span>
                                                                )}
                                                                {thread.card_title && <span>{description}</span>}
                                                                {thread.unread_mention && (
                                                                    <span className="font-semibold text-primary">
                                                                        {t("notification.inbox.mention")}
                                                                    </span>
                                                                )}
                                                                {thread.unread_events > 0 && (
                                                                    <span>
                                                                        {t("notification.inbox.unreadCount", { count: thread.unread_events })}
                                                                    </span>
                                                                )}
                                                                {thread.actor_name && <span className="break-words">{thread.actor_name}</span>}
                                                                <DateDistance date={new Date(thread.latest_at)} />
                                                            </div>
                                                            {!!subscriptionReasons?.length && (
                                                                <p className="mt-1 break-words text-xs text-muted-foreground">
                                                                    {t("notification.inbox.subscriptionReasons", {
                                                                        reasons: new Intl.ListFormat(i18n.resolvedLanguage).format(
                                                                            subscriptionReasons
                                                                        ),
                                                                    })}
                                                                </p>
                                                            )}
                                                        </div>
                                                        <div className="col-start-2 flex flex-wrap items-center gap-0.5 sm:col-start-auto">
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                className="gap-1"
                                                                aria-expanded={expanded === key}
                                                                onClick={() => setExpanded((current) => (current === key ? undefined : key))}
                                                            >
                                                                <IconComponent icon="list" className="size-4" aria-hidden />
                                                                {t("notification.inbox.activity")}
                                                            </Button>
                                                            {thread.unread_events > 0 && (
                                                                <Button
                                                                    size="icon"
                                                                    variant="ghost"
                                                                    aria-label={t("notification.Read notification")}
                                                                    title={t("notification.Read notification")}
                                                                    disabled={command.isPending}
                                                                    onClick={() => void apply([thread], "read")}
                                                                >
                                                                    <IconComponent icon="mail-open" className="size-4" aria-hidden />
                                                                </Button>
                                                            )}
                                                            {availableStates.map((value) => (
                                                                <Button
                                                                    key={value}
                                                                    size="icon"
                                                                    variant="ghost"
                                                                    aria-label={t(`notification.inbox.action.${value}`)}
                                                                    title={t(`notification.inbox.action.${value}`)}
                                                                    disabled={command.isPending}
                                                                    onClick={() => void apply([thread], value)}
                                                                >
                                                                    <IconComponent icon={stateIcons[value]} className="size-4" aria-hidden />
                                                                </Button>
                                                            ))}
                                                        </div>
                                                    </div>
                                                    {expanded === key && <NotificationActivity thread={thread} />}
                                                </li>
                                            );
                                        })}
                                    </ul>
                                </section>
                            );
                        })}
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
                    </div>
                </div>
            </div>
        </main>
    );
}
