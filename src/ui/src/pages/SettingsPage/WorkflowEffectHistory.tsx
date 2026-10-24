import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Table from "@/components/base/Table";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { formatDateTime, formatNumber } from "@/core/utils/LocaleFormat";

interface IEffectRun {
    uid: string;
    stage_revision: string;
    source: string;
    status: string;
    effects: string[];
    started_at: string;
    finished_at: string;
    duration_ms: number;
    result: { completed?: number; stopped?: number; notified?: number; accepted?: number; skipped?: number };
    project_uid: string;
    card_uid: string;
    actor_uid: string;
    actor_type: string;
    profile?: { uid: string; display_name: string } | null;
    error_code?: string | null;
}
interface IHistoryPage {
    items: IEffectRun[];
    next_cursor: string | null;
}

export default function WorkflowEffectHistory({ stageUID, actorUID, canRead }: { stageUID: string; actorUID: string; canRead: boolean }) {
    const [t, i18n] = useTranslation();
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const session = useAuthStore((state) => state.getSessionVersion());
    const currentUID = useAuthStore((state) => state.currentUser?.uid);
    const allowed = canRead && currentUID === actorUID;
    const query = useInfiniteQuery({
        queryKey: ["workflow-effect-history", actorUID, identity, session, stageUID],
        enabled: allowed,
        initialPageParam: undefined as string | undefined,
        queryFn: async ({ pageParam, signal }) => {
            const { data } = await api.get<IHistoryPage>(`/settings/workflow-stages/${encodeURIComponent(stageUID)}/history`, {
                params: { before: pageParam },
                signal,
            });
            const auth = useAuthStore.getState();
            if (
                signal.aborted ||
                auth.currentUser?.uid !== actorUID ||
                auth.getIdentityVersion() !== identity ||
                auth.getSessionVersion() !== session
            ) {
                throw new DOMException("Workflow history request cancelled", "AbortError");
            }
            return data;
        },
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        retry: false,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: true,
    });
    const effectLabel = (key: string) => {
        if (key === "complete_checkitems") return t("settings.Complete unchecked items");
        if (key === "stop_running_timers") return t("settings.Stop running timers");
        if (key === "notify_author_on_entry") return t("settings.Notify the author when someone else enters this stage");
        if (key === "compact_comments") return t("settings.Compact comments");
        if (key === "auto_archive") return t("settings.Auto archive cards");
        if (key.startsWith("bot:")) return `${t("settings.Bot")}: ${key.slice(4)}`;
        if (key === "card_moved") return t("settings.Card moved hook");
        if (key === "bot_cron_scheduled") return t("settings.Card schedule");
        return t("settings.Unknown effect");
    };
    const statusLabel = (status: string) => {
        const labels: Record<string, string> = {
            succeeded: "Succeeded",
            accepted: "Dispatch accepted",
            skipped: "Skipped",
            running: "Running",
            failed: "Failed",
            cancelled: "Cancelled",
            uncertain: "Uncertain",
        };
        return t(`settings.${labels[status] ?? "Unknown status"}`);
    };
    const date = (value: string) => {
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? t("settings.Unknown time") : formatDateTime(parsed, i18n.resolvedLanguage ?? i18n.language);
    };
    const duration = (milliseconds: number) =>
        formatNumber(milliseconds / 1000, i18n.language, {
            style: "unit",
            unit: "second",
            unitDisplay: "short",
            maximumFractionDigits: 3,
        });
    const rows = [...new Map(query.data?.pages.flatMap((page) => page.items).map((row) => [row.uid, row])).values()];
    if (!allowed)
        return (
            <p role="alert" className="mt-5 text-sm text-muted-foreground">
                {t("settings.History access unavailable")}
            </p>
        );
    return (
        <section className="mt-5 space-y-4" aria-label={t("settings.Effect history")} aria-busy={query.isFetching}>
            <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-muted-foreground">{t("settings.Only effects on cards you can currently read are shown.")}</p>
                <Button type="button" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
                    {t("common.Refresh")}
                </Button>
            </div>
            {query.isError ? (
                <div role="alert" className="space-y-2 text-sm">
                    <p>{t("settings.Could not load effect history")}</p>
                    <Button type="button" variant="outline" onClick={() => void query.refetch()}>
                        {t("common.Retry")}
                    </Button>
                </div>
            ) : query.isPending ? (
                <p role="status">{t("common.Loading...")}</p>
            ) : !rows.length ? (
                <p className="py-6 text-sm text-muted-foreground">{t("settings.No recorded effects")}</p>
            ) : (
                <Table.Root>
                    <Table.Header>
                        <Table.Row>
                            <Table.Head>{t("settings.Triggered")}</Table.Head>
                            <Table.Head>{t("settings.Source")}</Table.Head>
                            <Table.Head>{t("settings.Execution status")}</Table.Head>
                            <Table.Head className="hidden sm:table-cell">{t("settings.Duration")}</Table.Head>
                        </Table.Row>
                    </Table.Header>
                    <Table.Body>
                        {rows.map((row) => (
                            <Table.Row key={row.uid}>
                                <Table.Cell className="min-w-32 align-top sm:min-w-40">
                                    <time dateTime={row.started_at}>{date(row.started_at)}</time>
                                    <p className="mt-1 text-xs text-muted-foreground sm:hidden">
                                        {t("settings.Duration")}: {duration(row.duration_ms)}
                                    </p>
                                    <details className="mt-2 text-sm">
                                        <summary className="cursor-pointer">{t("settings.Effect details")}</summary>
                                        <p className="mt-2 break-all">
                                            {t("settings.Revision")}: {row.stage_revision}
                                        </p>
                                        <ul className="mt-2 space-y-1">
                                            {row.effects.map((key) => (
                                                <li key={key}>{effectLabel(key)}</li>
                                            ))}
                                        </ul>
                                        {row.result.accepted !== undefined && (
                                            <p>{t("settings.Dispatch accepted; see bot logs for execution details.")}</p>
                                        )}
                                        {row.result.completed !== undefined && (
                                            <p className="mt-2">
                                                {t("settings.Completed items")}: {formatNumber(row.result.completed, i18n.language)}
                                            </p>
                                        )}
                                        {row.result.stopped !== undefined && (
                                            <p>
                                                {t("settings.Stopped timers")}: {formatNumber(row.result.stopped, i18n.language)}
                                            </p>
                                        )}
                                        {row.result.notified !== undefined && (
                                            <p>
                                                {t("settings.Notifications created")}: {formatNumber(row.result.notified, i18n.language)}
                                            </p>
                                        )}
                                        {!["running", "uncertain"].includes(row.status) && (
                                            <p>
                                                {t("settings.Finished")}: {date(row.finished_at)}
                                            </p>
                                        )}
                                        <p>
                                            {row.actor_type === "profile"
                                                ? t("settings.Execution profile")
                                                : row.actor_type === "bot"
                                                  ? t("settings.Bot")
                                                  : t("settings.User")}
                                        </p>
                                        {row.profile && (
                                            <p>
                                                {t("settings.Execution profile")}: {row.profile.display_name}
                                            </p>
                                        )}
                                        {row.error_code && (
                                            <p>
                                                {t("settings.Error code")}: {row.error_code}
                                            </p>
                                        )}
                                        <a
                                            className="mt-2 inline-block underline"
                                            target="_blank"
                                            rel="noreferrer"
                                            href={`/board/${encodeURIComponent(row.project_uid)}/${encodeURIComponent(row.card_uid)}`}
                                        >
                                            {t("settings.Open card")}
                                        </a>
                                    </details>
                                </Table.Cell>
                                <Table.Cell className="align-top">
                                    {row.source === "entry"
                                        ? t("settings.Entry effects")
                                        : row.source === "bot_entry"
                                          ? t("settings.Entry bots")
                                          : row.source === "bot_interval"
                                            ? t("settings.Interval bots")
                                            : row.source === "continuous"
                                              ? t("settings.Continuous effects")
                                              : row.source === "manual"
                                                ? t("settings.Manual")
                                                : t("settings.Unknown source")}
                                </Table.Cell>
                                <Table.Cell className="align-top">{statusLabel(row.status)}</Table.Cell>
                                <Table.Cell className="hidden whitespace-nowrap align-top sm:table-cell">{duration(row.duration_ms)}</Table.Cell>
                            </Table.Row>
                        ))}
                    </Table.Body>
                </Table.Root>
            )}
            {!query.isError && query.hasNextPage && (
                <Button type="button" variant="outline" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>
                    {query.isFetchingNextPage ? t("common.Loading...") : t("settings.More history")}
                </Button>
            )}
        </section>
    );
}
