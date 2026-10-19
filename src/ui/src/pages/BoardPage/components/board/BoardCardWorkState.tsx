import type { IWorkState } from "@/core/models/ProjectCard";
import { useTranslation } from "react-i18next";

const reasonKeys: Record<string, string> = {
    dependency_unavailable: "Prerequisite unavailable",
    dependency_unfinished: "Prerequisite unfinished",
    approval_pending: "Decision required",
    verification_stale: "Verification is stale",
    workflow_policy_unavailable: "Workflow policy unavailable",
    workflow_unclassified: "Workflow not assigned",
    verification_evidence_unavailable: "Verification evidence missing",
    closed_with_open_checkitems: "Unfinished checklist",
    active_with_all_checkitems_completed: "Checklist complete; workflow still active",
    archived_with_running_timer: "Timer still running",
};

const reasonPriority = Object.keys(reasonKeys);
const neutralReasons = new Set([
    "dependencies_clear",
    "verification_recorded",
    "agent_lifecycle_unavailable",
    "blocker_policy_unavailable",
    "archived",
]);

function statusKey(state: IWorkState): string {
    if (state.lifecycle === "archived") return "archived";
    if (state.completed === true) return "completed";
    if (state.blocker_state === "blocked") return "blocked";
    if (state.blocker_state === "needs_approval") return "approval";
    if (state.blocker_state === "needs_input") return "input";
    if (state.execution_state === "failed") return "failed";
    if (state.execution_state === "paused") return "paused";
    if (state.execution_state === "queued") return "queued";
    if (state.material_kind !== "work") return "reference";
    if (["human_active", "agent_active", "external_active"].includes(state.execution_state ?? "") || state.workflow_stage === "active")
        return "active";
    if (state.workflow_stage === "review") return "review";
    return "pending";
}

/** Present the current server projection; never infer approval from progress. */
export default function BoardCardWorkState({ state }: { state: IWorkState | undefined }) {
    const [t] = useTranslation();
    if (!state) return null;

    const reason = state.state_inconsistency?.[0]?.code ?? reasonPriority.find((code) => state.reasons?.some((item) => item.code === code));
    // Never render raw messages/source refs: inaccessible prerequisites stay opaque.
    const reasonKey = reason
        ? (reasonKeys[reason] ?? "Status details unavailable")
        : state.reasons?.some((item) => !neutralReasons.has(item.code))
          ? "Status details unavailable"
          : undefined;
    const summary = [t(`card.Work state ${statusKey(state)}`), reasonKey && t(`card.${reasonKey}`)].filter(Boolean).join(" · ");

    return (
        <p data-board-card-work-state="" className="mt-1 truncate text-xs font-normal text-muted-foreground" title={summary}>
            <span className="sr-only">{t("card.Work state")}: </span>
            {summary}
        </p>
    );
}
