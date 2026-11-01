import { getDeadlinePressureLevel, isDeadlineWarningSuppressed } from "./BoardColumnCardStatus.ts";
import { matchesWorkload, type WorkloadColumn } from "../../../DashboardPage/components/ProjectWorkload.ts";

export const BOARD_DEADLINE_PRESETS = ["today", "week", "month", "quarter", "half-year", "year", "overdue", "none"] as const;
export type BoardDeadlinePreset = (typeof BOARD_DEADLINE_PRESETS)[number];
export const isBoardDeadlinePreset = (value: string): value is BoardDeadlinePreset => (BOARD_DEADLINE_PRESETS as readonly string[]).includes(value);

/** Local calendar intervals, inclusive start and exclusive end. Monday begins the week.
 * Calendar constructors preserve local midnight through DST, month and year rollover.
 */
export function deadlineCalendarRange(preset: Exclude<BoardDeadlinePreset, "overdue" | "none">, now: Date): [Date, Date] {
    const year = now.getFullYear();
    const month = now.getMonth();
    const day = now.getDate();
    switch (preset) {
        case "today":
            return [new Date(year, month, day), new Date(year, month, day + 1)];
        case "week": {
            const monday = day - ((now.getDay() + 6) % 7);
            return [new Date(year, month, monday), new Date(year, month, monday + 7)];
        }
        case "month":
            return [new Date(year, month, 1), new Date(year, month + 1, 1)];
        case "quarter": {
            const start = Math.floor(month / 3) * 3;
            return [new Date(year, start, 1), new Date(year, start + 3, 1)];
        }
        case "half-year": {
            const start = month < 6 ? 0 : 6;
            return [new Date(year, start, 1), new Date(year, start + 6, 1)];
        }
        case "year":
            return [new Date(year, 0, 1), new Date(year + 1, 0, 1)];
    }
}

type DeadlineCard = Parameters<typeof matchesWorkload>[0] & {
    deadline_at?: Date | null;
    archived_at?: Date | null;
    completed?: boolean;
    checklist_completed_count?: number;
    checklist_total_count?: number;
    work_state?: Parameters<typeof isDeadlineWarningSuppressed>[0]["workState"] & Parameters<typeof matchesWorkload>[0]["work_state"];
};

/** Date ranges include all card states; the existing board visibility rules still apply.
 * Overdue uses the existing warning's exact instant (including equality), excluding
 * finished, archived, reference and policy-suppressed work. Same-group choices OR.
 * An unknown URL value matches nothing rather than silently broadening the results.
 */
export function matchesBoardDeadline(
    card: DeadlineCard,
    column: WorkloadColumn | undefined,
    presets: readonly string[] | undefined,
    now: Date
): boolean {
    if (!presets?.length) return true;
    return presets.some((preset) => {
        if (!isBoardDeadlinePreset(preset)) return false;
        if (preset === "none") return !card.deadline_at;
        const deadline = card.deadline_at;
        if (!deadline || !Number.isFinite(deadline.getTime())) return false;
        if (preset === "overdue") {
            const suppressed = isDeadlineWarningSuppressed({
                archivedAt: card.archived_at,
                completed: card.completed,
                workState: card.work_state,
                checklist: { completed: card.checklist_completed_count ?? 0, total: card.checklist_total_count ?? 0 },
            });
            return matchesWorkload(card, column) && getDeadlinePressureLevel({ deadlineAt: deadline, isCompleted: suppressed, now }) === "overdue";
        }
        const [start, end] = deadlineCalendarRange(preset, now);
        return deadline >= start && deadline < end;
    });
}
