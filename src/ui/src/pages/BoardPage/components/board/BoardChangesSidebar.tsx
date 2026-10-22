import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import useGetCards from "@/controllers/api/board/useGetCards";
import useBoardUnreadCards from "./BoardUnreadCards";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { useTranslation } from "react-i18next";
import { formatDateDistance, formatDateTime } from "@/core/utils/LocaleFormat";

export default function BoardChangesSidebar({ projectUID, onNavigate }: { projectUID: string; onNavigate?: () => void }) {
    const [t, i18n] = useTranslation();
    const navigate = usePageNavigateRef();
    const { data, dataUpdatedAt, isFetching, refetch } = useGetCards(
        { project_uid: projectUID },
        {
            enabled: true,
            refetchOnMount: function refetchWhenUnowned(query) {
                // Reuse the active board snapshot; a sole observer must still refresh stale data on remount.
                return (
                    query.state.isInvalidated ||
                    query.state.status === "error" ||
                    !query.observers.some(
                        (observer) => observer.options.refetchOnMount !== refetchWhenUnowned && observer.getCurrentResult().isEnabled
                    )
                );
            },
        }
    );
    const unreadCards = useBoardUnreadCards(projectUID, data ? dataUpdatedAt : 0);
    const visibleCards = [...unreadCards].sort((a, b) => (b.last_change_seq ?? 0) - (a.last_change_seq ?? 0)).slice(0, 50);

    return (
        <section aria-label={t("dashboard.Changes")} className="h-full overflow-y-auto p-3">
            <h2 className="mb-3 flex items-center justify-between gap-2 text-sm font-semibold">
                <span>{t("dashboard.Changes")}</span>
                <Button type="button" size="sm" variant="ghost" disabled={isFetching} onClick={() => void refetch()}>
                    <IconComponent icon="refresh-cw" size="4" />
                    {t("dashboard.Refresh")}
                </Button>
            </h2>
            {isFetching && !visibleCards.length ? (
                <p role="status" className="text-sm text-muted-foreground">
                    {t("dashboard.Loading changes")}
                </p>
            ) : !visibleCards.length ? (
                <p className="text-sm text-muted-foreground">{t("dashboard.No unread changes")}</p>
            ) : (
                <div className="divide-y rounded-lg border">
                    {visibleCards.map((card) => (
                        <button
                            key={card.uid}
                            type="button"
                            className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left hover:bg-muted focus-visible:outline-primary"
                            onClick={() => {
                                navigate(ROUTES.BOARD.CARD(projectUID, card.uid));
                                onNavigate?.();
                            }}
                        >
                            <span className="flex w-full min-w-0 items-center gap-2">
                                <span className="size-2 shrink-0 rounded-full bg-primary" aria-hidden="true" />
                                <span className="min-w-0 flex-1 break-words text-sm">{card.title}</span>
                            </span>
                            <span className="shrink-0 text-xs text-muted-foreground">
                                {card.last_change_target_type === "comment" ? t("dashboard.Comment change") : t("dashboard.Card change")}
                            </span>
                            <span className="shrink-0 text-xs text-muted-foreground" data-unread-change-count>
                                {card.unread_change_count_status === "exact" &&
                                card.unread_change_count !== null &&
                                Number.isSafeInteger(card.unread_change_count) &&
                                card.unread_change_count >= 0
                                    ? t("dashboard.Unread change count", { count: card.unread_change_count })
                                    : card.unread_change_count_status === "manual_unread"
                                      ? t("dashboard.Marked unread")
                                      : t("dashboard.Change count unavailable")}
                            </span>
                            {card.last_change_at && (
                                <time
                                    dateTime={card.last_change_at.toISOString()}
                                    title={formatDateTime(card.last_change_at, i18n.language)}
                                    className="shrink-0 text-xs text-muted-foreground"
                                >
                                    {formatDateDistance(card.last_change_at, i18n.language)}
                                </time>
                            )}
                        </button>
                    ))}
                </div>
            )}
            {unreadCards.length > 50 && <p className="mt-2 text-xs text-muted-foreground">{t("dashboard.First 50 unread cards")}</p>}
        </section>
    );
}
