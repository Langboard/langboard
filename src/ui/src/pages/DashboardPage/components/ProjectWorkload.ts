export interface WorkloadColumn {
    uid: string;
    name: string;
    order: number;
    is_archive: boolean;
    workflow_stage?: string | null;
    workflow_counts_as_completed?: boolean | null;
    incomplete_count?: number;
    open_count?: number;
}

export function isUnfinishedColumn(column: Pick<WorkloadColumn, "name" | "is_archive" | "workflow_stage" | "workflow_counts_as_completed">): boolean {
    if (column.is_archive || column.workflow_stage === "reference") return false;
    if (typeof column.workflow_counts_as_completed === "boolean") return !column.workflow_counts_as_completed;
    if (column.workflow_stage === "closed") return false;
    // Display names are editable; only workflow policy determines completion.
    return true;
}

export function workloadColumns<TColumn extends WorkloadColumn>(columns: TColumn[]) {
    return columns.filter(isUnfinishedColumn).sort((a, b) => a.order - b.order);
}

export function workloadTotal(columns: WorkloadColumn[]): number | undefined {
    const visible = workloadColumns(columns);
    if (!columns.length || visible.some((column) => !Number.isSafeInteger(column.incomplete_count) || column.incomplete_count! < 0)) return undefined;
    return visible.reduce((total, column) => total + column.incomplete_count!, 0);
}

export function openWorkTotal(columns: WorkloadColumn[]): number | undefined {
    const visible = workloadColumns(columns);
    if (!columns.length || visible.some((column) => !Number.isSafeInteger(column.open_count) || column.open_count! < 0)) return undefined;
    // Authoritative open count excludes archived cards but retains completed checklists.
    return visible.reduce((total, column) => total + column.open_count!, 0);
}

export function workloadSearch(columnUID?: string): string {
    const filters = ["unfinished:yes"];
    if (columnUID) filters.push(`columns:${encodeURIComponent(encodeURIComponent(columnUID))}`);
    return `?${new URLSearchParams({ filters: filters.join(",") })}`;
}

export function matchesWorkload(
    card: {
        project_column_uid: string;
        source_type?: string | null;
        archived_at?: unknown;
        work_state?: { workflow_stage?: string | null; completed?: boolean | null; checklist_progress: { total: number; completed: number } };
    },
    column: Pick<WorkloadColumn, "name" | "is_archive" | "workflow_stage" | "workflow_counts_as_completed"> | undefined
): boolean {
    if (!column || !isUnfinishedColumn(column) || card.archived_at || card.source_type === "project_wiki" || card.source_type === "project_card")
        return false;
    // Column policy updates arrive independently of card projections. Retain a
    // legacy projection only while it still describes the current column stage.
    const projection = card.work_state;
    if (
        typeof column.workflow_counts_as_completed !== "boolean" &&
        (projection?.workflow_stage === undefined || projection.workflow_stage === column.workflow_stage) &&
        projection?.completed === true
    )
        return false;
    const progress = card.work_state?.checklist_progress;
    return !progress || progress.total === 0 || progress.completed < progress.total;
}
