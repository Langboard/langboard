import type { IWorkState, Interface as Card } from "@/core/models/ProjectCard";

export const BOARD_WORK_VIEWS = {
    staff: ["staff", "assigned", "review", "waiting", "blocked"],
    customer: ["requested", "reply", "processing", "completed"],
} as const;

type WorkViewCard = Pick<Card, "creator" | "archived_at"> & {
    member_uids?: readonly string[];
    work_state?: Pick<IWorkState, "version" | "lifecycle" | "material_kind" | "completed" | "workflow_stage" | "blocker_state">;
};

export function matchesBoardWorkView(card: WorkViewCard, actor: string, view: string): boolean {
    const state = card.work_state;
    if (card.archived_at || state?.lifecycle === "archived") return false;
    const requested = card.creator?.type === "user" && card.creator.uid === actor;
    if (view === "requested") return requested && state?.material_kind !== "wiki-like" && state?.material_kind !== "reference";
    if (state?.version !== 1 || state.lifecycle !== "active" || state.material_kind !== "work") return false;
    if (view === "completed") return requested && state.completed === true;
    if (state.completed !== false) return false;
    const waiting = state.workflow_stage === "waiting_customer_reply";
    const review = state.workflow_stage === "review";
    const blocked = state.blocker_state === "blocked";
    const assigned = card.member_uids?.includes(actor) ?? false;

    switch (view) {
        case "staff":
            return assigned || review || waiting || blocked;
        case "assigned":
            return assigned;
        case "review":
            return review;
        case "waiting":
            return waiting;
        case "blocked":
            return blocked;
        case "reply":
            return requested && waiting;
        case "processing":
            return requested && (state.workflow_stage === "active" || review);
        default:
            return false;
    }
}
