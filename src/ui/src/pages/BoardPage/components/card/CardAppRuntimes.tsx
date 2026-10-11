import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
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
}: {
    projectUID: string;
    cardUID: string;
    userUID: string;
    canRespond: boolean;
}) {
    const [t] = useTranslation();
    const [open, setOpen] = useState(false);
    const [sending, setSending] = useState(false);
    const [unresolved, setUnresolved] = useState(false);
    const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
    const cursor = cursors[cursors.length - 1];
    const path = `/board/${projectUID}/card/${cardUID}/apps/executions`;
    const query = useQuery({
        queryKey: ["card-app-runtimes", projectUID, cardUID, userUID, cursor],
        queryFn: async ({ signal }) =>
            (
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
    async function refresh() {
        const result = await query.refetch();
        if (!result.isError) setUnresolved(false);
    }
    async function control(item: Runtime, action: "stop" | "resume") {
        setSending(true);
        try {
            await api.post(`${path}/${item.request_uid}/${action}`, { generation: item.generation }, { env: { interceptToast: true } as never });
            // POST receipts are not runtime termination evidence. Read current state.
            const result = await query.refetch();
            setUnresolved(result.isError);
        } catch {
            setUnresolved(true); // No automatic second POST after an uncertain result.
        } finally {
            setSending(false);
        }
    }
    return (
        <div className="space-y-2">
            <Button size="sm" variant="ghost" aria-expanded={open} disabled={sending} onClick={() => setOpen(!open)}>
                {t("card.appRuntimes.title")}
            </Button>
            {open && (
                <>
                    <p className="text-sm text-muted-foreground">{t("card.appRuntimes.help")}</p>
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
                                        onClick={() => void control(item, "resume")}
                                    >
                                        {t("card.appRuntimes.resume")}
                                    </Button>
                                )}
                                {item.state === "authorized" && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={!canRespond || sending || unresolved}
                                        onClick={() => void control(item, "stop")}
                                    >
                                        {t("card.appRuntimes.stop")}
                                    </Button>
                                )}
                            </div>
                        ))}
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
