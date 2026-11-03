export const MY_WORK_GROUPS = ["overdue", "today", "upcoming", "review", "assigned", "mentioned", "created"] as const;
export type MyWorkGroup = (typeof MY_WORK_GROUPS)[number];
export type MyWorkOrder = "updated" | "priority";
export interface MyWorkFilter {
    projectUID: string | null;
    group: MyWorkGroup | null;
}
export interface MyWorkCard {
    uid: string;
    title: string;
    project_uid: string;
    project_title: string;
    project_column_name: string;
    deadline_at: string | null;
    updated_at: string;
    reasons: string[];
    group: MyWorkGroup;
}
export interface MyWorkPageResult {
    order?: MyWorkOrder;
    cards: MyWorkCard[];
    counts: Record<MyWorkGroup, number>;
    total: number;
    next_cursor: string | null;
    group: MyWorkGroup | null;
    project_uid: string | null;
    timezone: string;
    as_of: string;
    day_start: string;
    day_end: string;
    upcoming_end: string;
}
export const MY_WORK_LABELS: Record<MyWorkGroup, string> = {
    overdue: "Overdue",
    today: "Today",
    upcoming: "Upcoming",
    review: "Review / Check",
    assigned: "Assigned to me",
    mentioned: "Mentioned",
    created: "Created by me",
};
export function isMyWorkGroup(value: unknown): value is MyWorkGroup {
    return MY_WORK_GROUPS.includes(value as MyWorkGroup);
}
export function validateMyWorkPage(value: MyWorkPageResult, filter: MyWorkFilter, order: MyWorkOrder = "updated"): MyWorkPageResult {
    if (
        !value ||
        !Array.isArray(value.cards) ||
        (order === "priority" ? value.order !== order : value.order !== undefined && value.order !== order) ||
        value.cards.length > 50 ||
        !value.counts ||
        !MY_WORK_GROUPS.every((group) => Number.isSafeInteger(value.counts[group]) && value.counts[group] >= 0) ||
        value.total !== MY_WORK_GROUPS.reduce((total, group) => total + value.counts[group], 0) ||
        value.cards.length > (filter.group ? value.counts[filter.group] : value.total) ||
        value.group !== filter.group ||
        value.project_uid !== filter.projectUID ||
        !(
            value.next_cursor === null ||
            (typeof value.next_cursor === "string" && value.next_cursor.length > 0 && value.next_cursor.length <= 1024)
        ) ||
        value.cards.some(
            (card) =>
                !card ||
                ![card.uid, card.title, card.project_uid, card.project_title, card.project_column_name, card.updated_at].every(
                    (value) => typeof value === "string"
                ) ||
                !Number.isFinite(Date.parse(card.updated_at)) ||
                (card.deadline_at !== null && (typeof card.deadline_at !== "string" || !Number.isFinite(Date.parse(card.deadline_at)))) ||
                !isMyWorkGroup(card.group) ||
                (filter.group && card.group !== filter.group) ||
                (filter.projectUID && card.project_uid !== filter.projectUID)
        )
    ) {
        throw new Error("Invalid My Work summary response");
    }
    return value;
}
