import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import { Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";

interface Run {
    uid: string;
    source: "chat" | "schedule" | "workflow" | "log" | "mcp";
    status: string;
    created_at: string;
    detail?: {
        tool?: string;
        request_id?: string;
        payload_available?: boolean;
        output_text?: string;
        error_message?: string;
        messages?: { message: string; log_type: string; log_date: string }[];
        effects?: string[];
        result?: Record<string, number>;
        error_code?: string;
        attempt?: number;
        max_attempts?: number;
        card_uid?: string;
    };
}
interface RunPage {
    items: Run[];
    next_cursor: string | null;
}
const readable = (project?: Project.TModel) => project?.current_auth_role_actions?.some((action) => action === "read" || action === "*");

export default function BoardAutomationRuns({ project }: { project: Project.TModel }) {
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const session = useAuthStore((state) => state.getSessionVersion());
    const actions = project.useField("current_auth_role_actions");
    if (!actor || !readable(project)) return null;
    return (
        <Runs
            key={`${actor}:${identity}:${session}:${project.uid}:${actions?.join(",")}`}
            project={project}
            actor={actor}
            identity={identity}
            session={session}
            permissions={actions?.join(",") ?? ""}
        />
    );
}

function Runs({
    project,
    actor,
    identity,
    session,
    permissions,
}: {
    project: Project.TModel;
    actor: string;
    identity: number;
    session: number;
    permissions: string;
}) {
    const [t, i18n] = useTranslation();
    const [selected, select] = useState<Run>();
    const currentProject = useRef(project);
    currentProject.current = project;
    const read = async <T,>(path: string, signal: AbortSignal, before?: string) => {
        const { data } = await api.get<T>(`/board/${encodeURIComponent(project.uid)}/automation/runs${path}`, {
            params: before ? { before } : undefined,
            signal,
            env: { interceptToast: false } as never,
        });
        const auth = useAuthStore.getState();
        if (
            signal.aborted ||
            auth.currentUser?.uid !== actor ||
            auth.getIdentityVersion() !== identity ||
            auth.getSessionVersion() !== session ||
            currentProject.current !== project ||
            Project.Model.getModel(project.uid) !== project ||
            !readable(project) ||
            (project.current_auth_role_actions?.join(",") ?? "") !== permissions
        ) {
            throw new DOMException("Automation request cancelled", "AbortError");
        }
        return data;
    };
    const key = ["board-automation-runs", actor, identity, session, project.uid, permissions];
    const runs = useInfiniteQuery({
        queryKey: key,
        initialPageParam: undefined as string | undefined,
        queryFn: ({ signal, pageParam }) => read<RunPage>("", signal, pageParam),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        retry: false,
        gcTime: 0,
        staleTime: 0,
    });
    const detail = useQuery({
        queryKey: [...key, selected?.source, selected?.uid],
        enabled: !!selected,
        queryFn: ({ signal }) => read<{ item: Run }>(`/${selected!.source}/${encodeURIComponent(selected!.uid)}`, signal),
        retry: false,
        gcTime: 0,
        staleTime: 0,
    });
    const rows = [...new Map(runs.data?.pages.flatMap((page) => page.items).map((row) => [`${row.source}:${row.uid}`, row])).values()];
    const sources = { chat: "Owned board chat", schedule: "Schedule dispatch", workflow: "Workflow effect", log: "Bot log", mcp: "Native MCP tool" };
    const statuses: Record<string, string> = {
        accepted: "Dispatch accepted",
        streaming: "Running",
        resuming: "Running",
        running: "Running",
        dispatching: "Dispatching",
        awaiting_approval: "Awaiting approval",
        pending: "Pending",
        completed: "Completed",
        succeeded: "Succeeded",
        failed: "Failed",
        cancelled: "Cancelled",
        uncertain: "Uncertain",
        skipped: "Skipped",
        info: "Log information",
        success: "Log success",
        error: "Log error",
        applied: "Applied",
        not_applied: "Not applied",
        unknown: "Outcome unknown",
        denied: "Denied",
    };
    const statusLabel = (run: Run) => {
        const mcpStatuses: Record<string, string> = { success: "Succeeded", error: "Failed" };
        return (run.source === "mcp" ? mcpStatuses[run.status] : undefined) ?? statuses[run.status] ?? "Unknown status";
    };
    const time = (value: string) => {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? t("project.Unknown time") : date.toLocaleString(i18n.resolvedLanguage ?? i18n.language);
    };
    const error = (retry: () => void) => (
        <div role="alert">
            <p>{t("project.Could not load automation runs")}</p>
            <Button variant="outline" onClick={retry}>
                {t("common.Retry")}
            </Button>
        </div>
    );
    const selectedDetail = detail.data?.item.detail;
    return (
        <section aria-label={t("project.Recent automation runs")} className="space-y-3 p-3" aria-busy={runs.isFetching}>
            <h2 className="font-semibold">{t("project.Recent automation runs")}</h2>
            <p className="text-xs text-muted-foreground">
                {t("project.Native receipts only. Dispatch acceptance does not confirm execution. Log severity is not execution status.")}
            </p>
            <Button variant="outline" disabled={runs.isFetching} onClick={() => void runs.refetch()}>
                {t("common.Refresh")}
            </Button>
            {runs.isError ? (
                error(() => void runs.refetch())
            ) : runs.isPending ? (
                <p role="status">{t("common.Loading...")}</p>
            ) : rows.length === 0 ? (
                <p>{t("project.No authorized automation runs")}</p>
            ) : (
                <ul className="space-y-3">
                    {rows.map((row) => (
                        <li key={`${row.source}:${row.uid}`} className="rounded border p-2 text-sm">
                            <p>{t(`project.${sources[row.source]}`)}</p>
                            <p>{t(`project.${statusLabel(row)}`)}</p>
                            <time dateTime={row.created_at} className="block break-words text-xs text-muted-foreground">
                                {time(row.created_at)}
                            </time>
                            <Button variant="link" className="h-auto px-0 py-2" onClick={() => select(row)}>
                                {t("project.View run details")}
                            </Button>
                        </li>
                    ))}
                </ul>
            )}
            {!runs.isError && runs.hasNextPage && (
                <Button variant="outline" disabled={runs.isFetching} onClick={() => void runs.fetchNextPage()}>
                    {t("project.More runs")}
                </Button>
            )}
            <Dialog.Root
                open={!!selected}
                onOpenChange={(open) => {
                    if (!open) select(undefined);
                }}
            >
                <Dialog.Content className="max-w-3xl" viewportClassName="max-h-[75vh] overflow-auto">
                    <Dialog.Header>
                        <Dialog.Title>{t("project.Automation run details")}</Dialog.Title>
                        <Dialog.Description>{t("project.Details reflect your current source permissions.")}</Dialog.Description>
                    </Dialog.Header>
                    <Button variant="outline" disabled={detail.isFetching} onClick={() => void detail.refetch()}>
                        {t("common.Refresh")}
                    </Button>
                    {detail.isError ? (
                        error(() => void detail.refetch())
                    ) : detail.isPending ? (
                        <p role="status">{t("common.Loading...")}</p>
                    ) : (
                        selectedDetail && (
                            <div className="space-y-3 break-words text-sm">
                                <p>{t(`project.${statusLabel(detail.data.item)}`)}</p>
                                {selectedDetail.payload_available === false && (
                                    <p>{t("project.MCP metadata only. Arguments and results are not stored.")}</p>
                                )}
                                {selectedDetail.tool && <p>{selectedDetail.tool}</p>}
                                {selectedDetail.request_id && <p>{selectedDetail.request_id}</p>}
                                {selectedDetail.output_text && <pre className="whitespace-pre-wrap break-words">{selectedDetail.output_text}</pre>}
                                {selectedDetail.error_message && <p>{selectedDetail.error_message}</p>}
                                {selectedDetail.error_code && (
                                    <p>
                                        {t("project.Error code")}: {selectedDetail.error_code}
                                    </p>
                                )}
                                {selectedDetail.messages?.map((message, index) => (
                                    <div key={index}>
                                        <time>{time(message.log_date)}</time>
                                        <pre className="whitespace-pre-wrap break-words">{message.message}</pre>
                                    </div>
                                ))}
                                {selectedDetail.effects && (
                                    <ul>
                                        {selectedDetail.effects.map((effect) => (
                                            <li key={effect}>{effect}</li>
                                        ))}
                                    </ul>
                                )}
                                {selectedDetail.result && <pre className="whitespace-pre-wrap">{JSON.stringify(selectedDetail.result, null, 2)}</pre>}
                                {selectedDetail.attempt !== undefined && (
                                    <p>
                                        {t("project.Attempts")}: {selectedDetail.attempt} / {selectedDetail.max_attempts}
                                    </p>
                                )}
                                {selectedDetail.card_uid && (
                                    <a
                                        className="underline"
                                        href={`/board/${encodeURIComponent(project.uid)}/${encodeURIComponent(selectedDetail.card_uid)}`}
                                    >
                                        {t("project.Open card")}
                                    </a>
                                )}
                            </div>
                        )
                    )}
                </Dialog.Content>
            </Dialog.Root>
        </section>
    );
}
