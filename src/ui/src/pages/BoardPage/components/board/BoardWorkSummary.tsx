import { useReducer } from "react";
import { useTranslation } from "react-i18next";
import { ProjectCard } from "@/core/models";
import { useBoard } from "@/core/providers/BoardProvider";
import type { IBoardCardsSnapshot } from "@/controllers/api/board/useGetCards";

/** Membership comes from the authorized response; current fields may change through sockets. */
export function BoardSnapshotSummary({ snapshot, cards }: { snapshot?: IBoardCardsSnapshot; cards: ProjectCard.TModel[] }) {
    const [t, i18n] = useTranslation();
    const [, refresh] = useReducer((value: number) => value + 1, 0);
    if (!snapshot) return <p data-board-work-summary="">{t("board.Snapshot unavailable", { defaultValue: "Snapshot unavailable" })}</p>;

    const membership = new Set(snapshot.cardUIDs);
    const loaded = cards.filter((card) => membership.has(card.uid) && card.project_uid === snapshot.projectUID);
    let assigned = 0;
    let review = 0;
    let blocked = 0;
    let unknown = 0;
    for (const card of loaded) {
        const state = card.work_state;
        if (!state || state.version !== 1 || state.lifecycle == null || state.material_kind == null || state.completed == null) {
            unknown += 1;
            continue;
        }
        if (state.lifecycle !== "active" || state.material_kind !== "work" || state.completed) continue;
        // Assignment backlog includes blocked work; execution readiness is a separate projection.
        if (card.member_uids?.includes(snapshot.actorUID)) assigned += 1;
        if (state.workflow_stage === "review") review += 1;
        if (state.blocker_state === "blocked") blocked += 1;
        if (state.workflow_stage == null || state.blocker_state == null) unknown += 1;
    }
    const missing = membership.size - loaded.length;
    return (
        <div
            data-board-work-summary=""
            role="group"
            aria-label={t("board.Work summary", { defaultValue: "Work summary" })}
            title={new Date(snapshot.receivedAt).toLocaleString(i18n.language)}
            className="text-xs text-muted-foreground"
        >
            {loaded.map((card) => (
                <SummaryCardSubscription key={card.uid} card={card} refresh={refresh} />
            ))}
            <p>
                {t("board.Loaded snapshot", { defaultValue: "Loaded snapshot" })}: {loaded.length}/{membership.size}
                <time className="sr-only" dateTime={new Date(snapshot.receivedAt).toISOString()}>
                    {new Date(snapshot.receivedAt).toLocaleString(i18n.language)}
                </time>
                {" · "}
                {t("board.Assigned outstanding work", { defaultValue: "Assigned outstanding work" })}: {assigned}
                {" · "}
                {t("board.Review", { defaultValue: "Review" })}: {review}
                {" · "}
                {t("board.Blocked", { defaultValue: "Blocked" })}: {blocked}
                {unknown > 0 && (
                    <>
                        {" "}
                        · {t("board.Unknown work state", { defaultValue: "Unknown work state" })}: {unknown}
                    </>
                )}
                {missing > 0 && (
                    <>
                        {" "}
                        · {t("board.Snapshot cards missing", { defaultValue: "Snapshot cards missing" })}: {missing}
                    </>
                )}
            </p>
        </div>
    );
}

function SummaryCardSubscription({ card, refresh }: { card: ProjectCard.TModel; refresh: () => void }) {
    card.useField("work_state", refresh);
    card.useField("project_uid", refresh);
    card.useField("member_uids", refresh);
    return null;
}

export default function BoardWorkSummary() {
    const { cardsSnapshot, cards } = useBoard();
    return <BoardSnapshotSummary snapshot={cardsSnapshot} cards={cards} />;
}
