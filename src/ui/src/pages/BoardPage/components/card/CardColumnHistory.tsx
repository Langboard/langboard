import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import { api } from "@/core/helpers/Api";
import { ProjectCard } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useAuthStore from "@/core/stores/AuthStore";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { Routing, SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import CardColumnEvent, { ColumnEvent } from "./CardColumnEvent";

export default function CardColumnHistory({ card }: { card: ProjectCard.TModel }) {
    const { projectUID, socket, currentUser, hasRoleAction } = useBoardCard();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const canRead = actorUID === currentUser.uid && hasRoleAction(ProjectRole.EAction.Read);
    const columnUID = card.useField("project_column_uid");
    const key = `${currentUser.uid}/${sessionVersion}/${projectUID}/${card.uid}`;
    const [selection, setSelection] = useState<{ key: string; before?: string }>();
    const before = selection?.key === key ? selection.before : undefined;
    const [page, setPage] = useState<{ key: string; records: ColumnEvent[]; next_cursor?: string | null }>();
    const events = page?.key === key ? page.records.filter((event) => event.column?.name) : [];
    const nextCursor = page?.key === key ? page.next_cursor : undefined;
    const [t] = useTranslation();

    useEffect(() => {
        let controller: AbortController | undefined;
        setPage(undefined);
        if (!canRead) {
            setSelection(undefined);
            return;
        }
        const url = Utils.String.format(Routing.API.ACTIVITIY.CARD_COLUMN_HISTORY, { uid: projectUID, card_uid: card.uid });
        const refresh = () => {
            controller?.abort();
            const request = new AbortController();
            controller = request;
            void api
                .get<{ records: ColumnEvent[]; next_cursor?: string | null }>(url, {
                    params: { before_activity_uid: before },
                    signal: request.signal,
                    env: { interceptToast: true } as never,
                })
                .then(({ data }) => {
                    if (!request.signal.aborted) setPage({ ...data, key });
                })
                .catch(() => {
                    if (!request.signal.aborted) setPage(undefined);
                });
        };
        // Column changes can precede the asynchronous activity write. Refresh after its committed event too.
        // The dashboard project loader owns this subscription; only add a listener here.
        const listener = {
            topic: ESocketTopic.Dashboard as const,
            topicId: projectUID,
            event: SocketEvents.SERVER.DASHBOARD.PROJECT.ACTIVITY_RECORDED.replace("{uid}", projectUID),
            eventKey: `card-column-history-${projectUID}-${card.uid}`,
            callback: refresh,
        };
        socket.on(listener);
        refresh();
        return () => {
            controller?.abort();
            socket.off(listener);
        };
    }, [projectUID, card, columnUID, socket, key, before, canRead]);

    if (!canRead || (!events.length && !before && !nextCursor)) return null;

    return (
        <nav aria-label={t("card.Activity")} className="flex min-w-0 flex-wrap items-center gap-x-1 gap-y-0.5 text-[11px] text-muted-foreground/80">
            {before && (
                <Button type="button" variant="ghost" className="h-5 px-1 text-[11px]" onClick={() => setSelection({ key })}>
                    {t("card.Latest history")}
                </Button>
            )}
            {nextCursor && (
                <Button type="button" variant="ghost" className="h-5 px-1 text-[11px]" onClick={() => setSelection({ key, before: nextCursor })}>
                    {t("card.Older history")}
                </Button>
            )}
            <IconComponent icon="history" size="3" className="shrink-0 opacity-70" aria-hidden="true" />
            {events.map((event, index) => {
                return (
                    <span key={event.uid} className="inline-flex min-w-0 items-center gap-1">
                        {index > 0 && (
                            <span aria-hidden="true" className="opacity-50">
                                ›
                            </span>
                        )}
                        <CardColumnEvent event={event} />
                    </span>
                );
            })}
        </nav>
    );
}
