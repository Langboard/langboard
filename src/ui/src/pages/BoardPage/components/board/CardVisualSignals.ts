import type { TCardPriority } from "@langboard/core/constants";
import {
    calculateDeadlinePressure,
    getDeadlinePressureLevel,
    isDeadlineWarningSuppressed,
    type IBoardCardChecklistProgress,
} from "./BoardColumnCardStatus.ts";

export type TCardMotion = "none" | "critical" | "overdue" | "emergency" | "agent";

/** Shared arbitration for board, detail and compact cards. Static signals survive motion suppression. */
export function getCardVisualSignals({
    priority = "P2",
    deadlineAt,
    archivedAt,
    checklist,
    completed,
    workState,
    agentRunning = false,
    dragging = false,
    reducedMotion = false,
    visible = true,
    now,
}: {
    priority?: TCardPriority;
    deadlineAt?: Date | null;
    archivedAt?: Date | null;
    checklist: Pick<IBoardCardChecklistProgress, "completed" | "total">;
    completed?: boolean;
    workState?: { completed?: boolean | null; overdue_suppressed?: boolean | null; lifecycle?: "active" | "archived" };
    agentRunning?: boolean;
    dragging?: boolean;
    reducedMotion?: boolean;
    visible?: boolean;
    now: Date;
}) {
    const suppressed = isDeadlineWarningSuppressed({ archivedAt, checklist, completed, workState });
    const deadlineLevel = getDeadlinePressureLevel({ deadlineAt, isCompleted: suppressed, now });
    let motion: TCardMotion = "none";
    if (!suppressed && !dragging && !reducedMotion && visible) {
        if (priority === "P0") motion = "critical";
        else if (deadlineLevel === "overdue") motion = "overdue";
        else if (priority === "P1") motion = "emergency";
        else if (agentRunning) motion = "agent";
    }
    return {
        priority,
        suppressed,
        deadlineLevel,
        deadlinePressure: calculateDeadlinePressure({ deadlineAt, isCompleted: suppressed, now }),
        agentRunning,
        motion,
    };
}
