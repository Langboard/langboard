import { isAxiosError } from "axios";
import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface Runtime {
    request_uid: string;
    app_key: string;
    generation: number;
    current_generation?: number;
    resume_request_uid?: string | null;
    state: "awaiting_permit" | "authorized" | "stop_requested" | "stopped";
}
export default function CardAppRuntimes({
    projectUID,
    cardUID,
    userUID,
    canRespond,
    panelContext,
    onAuthorityDenied,
}: {
    projectUID: string;
    cardUID: string;
    userUID: string;
    canRespond: boolean;
    panelContext?: { appKey: string; app_revision: string; binding_revision: string };
    onAuthorityDenied?: () => void;
}) {
    const [t] = useTranslation();
    const [open, setOpen] = useState(!!panelContext);
    const [sending, setSending] = useState(false);
    const writing = useRef(false);
    const [unresolved, setUnresolved] = useState(false);
    const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
    const cursor = cursors[cursors.length - 1];
    const path = `/board/${projectUID}/card/${cardUID}/apps/executions`;
    const panelPath = panelContext ? `/board/${projectUID}/apps/${encodeURIComponent(panelContext.appKey)}/panel` : null;
    const revisions = panelContext ? { app_revision: panelContext.app_revision, binding_revision: panelContext.binding_revision } : {};
    const [confirm, setConfirm] = useState<{ item: Runtime; action: "stop" | "resume" } | null>(null);
    const query = useQuery({
        queryKey: ["card-app-runtimes", projectUID, cardUID, userUID, cursor, panelContext],
        queryFn: async ({ signal }) =>
            panelPath
                ? (
                      await api.post<{ items: Runtime[]; next_cursor?: string | null }>(
                          `${panelPath}/runtimes`,
                          { ...revisions, card_uid: cardUID, cursor },
                          { signal, env: { interceptToast: true } as never }
                      )
                  ).data
                : (
                      await api.get<{ items: Runtime[]; next_cursor?: string | null }>(path, {
                          signal,
                          params: { cursor },
                          env: { interceptToast: true } as never,
                      })
                  ).data,
        enabled: open,
        retry: 0,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    useEffect(() => {
        if (panelContext && isAxiosError(query.error) && [401, 403, 404, 409].includes(query.error.response?.status ?? 0)) onAuthorityDenied?.();
    }, [query.error, panelContext, onAuthorityDenied]);
    async function refresh() {
        const result = await query.refetch();
        if (!result.isError) {
            setUnresolved(false);
            setConfirm(null);
        }
    }
    async function control(item: Runtime, action: "stop" | "resume") {
        if (writing.current || sending || unresolved || query.isFetching || query.isError || !canRespond) return;
        writing.current = true;
        setSending(true);
        try {
            await api.post(
                panelPath ? `${panelPath}/cards/${cardUID}/runtimes/${item.request_uid}/${action}` : `${path}/${item.request_uid}/${action}`,
                { ...revisions, generation: item.generation },
                { env: { interceptToast: true } as never }
            );
            // POST receipts are not runtime termination evidence. Read current state.
            const result = await query.refetch();
            setUnresolved(result.isError);
            setConfirm(null);
        } catch (error) {
            if (panelContext && isAxiosError(error) && [401, 403, 404, 409].includes(error.response?.status ?? 0)) onAuthorityDenied?.();
            setUnresolved(true); // No automatic second POST after an uncertain result.
        } finally {
            writing.current = false;
            setSending(false);
        }
    }
    return (
        <div className="space-y-2">
            {!panelContext && (
                <Button size="sm" variant="ghost" aria-expanded={open} disabled={sending} onClick={() => setOpen(!open)}>
                    {t("card.appRuntimes.title")}
                </Button>
            )}
            {open && (
                <>
                    {!panelContext && <p className="text-sm text-muted-foreground">{t("card.appRuntimes.help")}</p>}
                    <Button size="sm" variant="outline" disabled={sending || query.isFetching} onClick={() => void refresh()}>
                        {t("card.appInteractions.refresh")}
                    </Button>
                    {(unresolved || query.isError) && <p role="alert">{t("card.appRuntimes.unresolved")}</p>}
                    {query.isFetching && <p role="status">{t("common.Loading...")}</p>}
                    {!query.isError &&
                        !query.isFetching &&
                        query.data?.items.map((item) => (
                            <div key={item.request_uid} className="space-y-2 rounded border p-2">
                                <p className="break-all text-sm">
                                    {item.app_key} · {t("card.appRuntimes.generation", { count: item.generation })}
                                </p>
                                <p role="status" className="text-sm">
                                    {t(`card.appRuntimes.states.${item.state}`)}
                                </p>
                                {item.resume_request_uid && (
                                    <p role="status" className="text-sm">
                                        {t("card.appRuntimes.resumeRequested")}
                                    </p>
                                )}
                                {item.state === "stopped" && !item.resume_request_uid && (item.current_generation ?? 0) > item.generation && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={!canRespond || sending || unresolved}
                                        onClick={() => setConfirm({ item, action: "resume" })}
                                    >
                                        {t("card.appRuntimes.resume")}
                                    </Button>
                                )}
                                {item.state === "authorized" && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={!canRespond || sending || unresolved}
                                        onClick={() => setConfirm({ item, action: "stop" })}
                                    >
                                        {t("card.appRuntimes.stop")}
                                    </Button>
                                )}
                            </div>
                        ))}
                    {confirm && (
                        <div className="space-y-2 rounded border p-3">
                            <p>{t("card.appRuntimes.help")}</p>
                            <Button
                                size="sm"
                                disabled={sending || unresolved || query.isError || query.isFetching || !canRespond}
                                onClick={() => void control(confirm.item, confirm.action)}
                            >
                                {t(`card.appRuntimes.${confirm.action}`)}
                            </Button>
                            <Button size="sm" variant="ghost" disabled={sending} onClick={() => setConfirm(null)}>
                                {t("common.Cancel")}
                            </Button>
                        </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                        {cursors.length > 1 && (
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={sending || query.isFetching || unresolved}
                                onClick={() => setCursors(cursors.slice(0, -1))}
                            >
                                {t("card.appInteractions.previous")}
                            </Button>
                        )}
                        {query.data?.next_cursor && !query.isError && (
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={sending || query.isFetching || unresolved}
                                onClick={() => setCursors([...cursors, query.data!.next_cursor!])}
                            >
                                {t("card.appInteractions.next")}
                            </Button>
                        )}
                    </div>
                    {!query.isError && !query.isFetching && query.data?.items.length === 0 && (
                        <p className="text-sm text-muted-foreground">{t("card.appRuntimes.empty")}</p>
                    )}
                </>
            )}
        </div>
    );
}
