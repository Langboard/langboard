import { workflowActionLabels, workflowConditionLabels, workflowEventLabel } from "./WorkflowRuleEditor";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Table from "@/components/base/Table";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { formatDateTime, formatNumber } from "@/core/utils/LocaleFormat";
import type { IWorkflowRule, TWorkflowAction, TWorkflowCondition } from "@/controllers/api/settings/workflowStages/useWorkflowStages";

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
type TAdmittedRuleSnapshot = Readonly<Omit<IWorkflowRule, "uid" | "revision" | "conditions" | "actions">> & {
    readonly stage_key: string;
    readonly uid: string;
    readonly revision: number;
    readonly conditions: ReadonlyArray<Readonly<TWorkflowCondition>>;
    readonly actions: ReadonlyArray<Readonly<TWorkflowAction & { uid: string }>>;
};
interface IActionAttempt {
    attempt: number;
    status: string;
    started_at: string | null;
    finished_at: string | null;
    error_code: string | null;
}
interface IRuleRun {
    uid: string;
    rule_uid: string;
    rule_revision: number;
    configured_by_uid: string | null;
    rule_snapshot: TAdmittedRuleSnapshot;
    event_id: string;
    event_type: string;
    occurred_at: string;
    actor_uid: string | null;
    actor_type: string;
    project_uid: string;
    card_uid: string;
    residency: string;
    condition_results: { kind: string; matched: boolean }[];
    status: string;
    actions: {
        uid: string;
        action_uid: string;
        kind: string;
        executor_uid: string | null;
        executor_type: string;
        status: string;
        attempt: number;
        attempts?: IActionAttempt[];
        idempotency_key: string;
        result: Record<string, unknown>;
        error_code: string | null;
        started_at: string | null;
        finished_at: string | null;
    }[];
}
interface IHistoryPage {
    items: IRuleRun[];
    legacy_items: IEffectRun[];
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
            queued: "Queued",
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
    const snapshotDescription = t("settings.Configuration captured when this run was admitted. Later rule edits do not change this evidence.");
    const actionLabel = (kind: string) => t(`settings.${workflowActionLabels[kind as keyof typeof workflowActionLabels] ?? "Unknown effect"}`);
    const elapsed = (started: string | null, finished: string | null) => {
        const milliseconds = started && finished ? Date.parse(finished) - Date.parse(started) : NaN;
        return Number.isFinite(milliseconds) && milliseconds >= 0 ? duration(milliseconds) : t("settings.Duration unavailable");
    };
    const executorLabel = (run: IRuleRun, action: IRuleRun["actions"][number]) => {
        const captured = run.rule_snapshot?.actions.find((item) => item.uid === action.action_uid);
        const label =
            captured?.executor.kind === "compactor"
                ? "Dedicated compactor"
                : action.executor_type === "bot"
                  ? "Bot"
                  : action.executor_type === "user"
                    ? "User"
                    : "Execution profile";
        return `${t(`settings.${label}`)}${action.executor_uid ? ` ${action.executor_uid}` : ""}`;
    };
    const runs = [...new Map(query.data?.pages.flatMap((page) => page.items).map((row) => [row.uid, row])).values()];
    const rows = [...new Map(query.data?.pages.flatMap((page) => page.legacy_items).map((row) => [row.uid, row])).values()];
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
            ) : !rows.length && !runs.length ? (
                <p className="py-6 text-sm text-muted-foreground">{t("settings.No recorded effects")}</p>
            ) : (
                <div className="space-y-4">
                    {runs.map((run) => (
                        <article key={run.uid} className="space-y-2 rounded-md border p-3 text-sm">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <time dateTime={run.occurred_at}>{date(run.occurred_at)}</time>
                                <span>{statusLabel(run.status)}</span>
                            </div>
                            <p className="break-all">
                                {t("settings.Rule")}: {run.rule_snapshot?.name ? `${run.rule_snapshot.name} (${run.rule_uid})` : run.rule_uid} ·{" "}
                                {t("settings.Revision")}: {run.rule_revision}
                            </p>
                            <p className="break-all">
                                {t("settings.Trigger")}: {workflowEventLabel(run.event_type, t)} · {run.event_id}
                            </p>
                            <p className="break-all">
                                {t("settings.Actor")}: {run.actor_type} {run.actor_uid}
                            </p>
                            <p className="break-all">
                                {t("settings.Settings author")}: {run.configured_by_uid ?? t("settings.Unknown settings author")}
                            </p>
                            <details>
                                <summary className="cursor-pointer">{t("settings.Rule details")}</summary>
                                <section className="mt-2 space-y-1" aria-label={t("settings.Historical rule snapshot")}>
                                    <p className="font-medium">{t("settings.Historical rule snapshot")}</p>
                                    <p className="text-xs text-muted-foreground">{snapshotDescription}</p>
                                    <pre className="max-w-full whitespace-pre-wrap break-words rounded-md border p-2 text-xs">
                                        {JSON.stringify(run.rule_snapshot, null, 2)}
                                    </pre>
                                </section>
                                <p>
                                    {t("settings.Stage residency")}: {date(run.residency)}
                                </p>
                                <ul>
                                    {run.condition_results.map((condition, index) => (
                                        <li key={index}>
                                            {workflowConditionLabels[condition.kind as keyof typeof workflowConditionLabels]
                                                ? t(`settings.${workflowConditionLabels[condition.kind as keyof typeof workflowConditionLabels]}`)
                                                : condition.kind}
                                            : {t(condition.matched ? "settings.Matched" : "settings.Not matched")}
                                        </li>
                                    ))}
                                </ul>
                                {run.actions.map((action) => (
                                    <div key={action.uid} className="mt-2 space-y-1 rounded-md border p-2">
                                        <p className="break-all">
                                            {actionLabel(action.kind)} · {statusLabel(action.status)}
                                        </p>
                                        <p className="break-all">
                                            {t("settings.Executor")}: {executorLabel(run, action)}
                                        </p>
                                        <p>
                                            {t("settings.Attempt")}: {action.attempt}
                                        </p>
                                        <p className="break-all">
                                            {t("settings.Action")}: {action.action_uid} · {action.idempotency_key}
                                        </p>
                                        {action.started_at && (
                                            <p>
                                                {t("settings.Started")}: {date(action.started_at)}
                                            </p>
                                        )}
                                        {action.finished_at && (
                                            <p>
                                                {t("settings.Finished")}: {date(action.finished_at)}
                                            </p>
                                        )}
                                        <p>
                                            {t("settings.Duration")}: {elapsed(action.started_at, action.finished_at)}
                                        </p>
                                        {!!action.attempts?.length && (
                                            <section aria-label={t("settings.Attempt history")} className="space-y-1">
                                                <p className="font-medium">{t("settings.Attempt history")}</p>
                                                <ol className="space-y-2">
                                                    {action.attempts.map((attempt, index) => (
                                                        <li key={`${attempt.attempt}-${index}`} className="rounded-md border p-2">
                                                            <p>
                                                                {t("settings.Attempt")}: {attempt.attempt} · {statusLabel(attempt.status)}
                                                            </p>
                                                            {attempt.started_at && (
                                                                <p>
                                                                    {t("settings.Started")}: {date(attempt.started_at)}
                                                                </p>
                                                            )}
                                                            {attempt.finished_at && (
                                                                <p>
                                                                    {t("settings.Finished")}: {date(attempt.finished_at)}
                                                                </p>
                                                            )}
                                                            <p>
                                                                {t("settings.Duration")}: {elapsed(attempt.started_at, attempt.finished_at)}
                                                            </p>
                                                            {attempt.error_code && (
                                                                <p className="break-all">
                                                                    {t("settings.Error code")}: {attempt.error_code}
                                                                </p>
                                                            )}
                                                        </li>
                                                    ))}
                                                </ol>
                                            </section>
                                        )}
                                        {action.status === "accepted" && (
                                            <p>{t("settings.Dispatch accepted; see bot logs for execution details.")}</p>
                                        )}
                                        {action.error_code && (
                                            <p>
                                                {t("settings.Error code")}: {action.error_code}
                                            </p>
                                        )}
                                        <p className="break-all">
                                            {t("settings.Result references")}: {JSON.stringify(action.result)}
                                        </p>
                                    </div>
                                ))}
                            </details>
                            <a
                                className="inline-block underline"
                                target="_blank"
                                rel="noreferrer"
                                href={`/board/${encodeURIComponent(run.project_uid)}/${encodeURIComponent(run.card_uid)}`}
                            >
                                {t("settings.Open card")}
                            </a>
                        </article>
                    ))}
                    {!!rows.length && <p className="text-sm text-muted-foreground">{t("settings.Preserved historical effects (read only)")}</p>}
                    {!!rows.length && (
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
                                        <Table.Cell className="hidden whitespace-nowrap align-top sm:table-cell">
                                            {duration(row.duration_ms)}
                                        </Table.Cell>
                                    </Table.Row>
                                ))}
                            </Table.Body>
                        </Table.Root>
                    )}
                </div>
            )}
            {!query.isError && query.hasNextPage && (
                <Button type="button" variant="outline" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>
                    {query.isFetchingNextPage ? t("common.Loading...") : t("settings.More history")}
                </Button>
            )}
        </section>
    );
}
