import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";

import { DomainEventType } from "@/core/events/DomainEventType";

export type TWorkflowTrigger =
    | { kind: "stage_entered" }
    | { kind: "event"; event_type: DomainEventType }
    | { kind: "interval"; interval_seconds: number };
export type TConditionKind =
    | "stage_residency_seconds"
    | "uncompacted_characters"
    | "uncompacted_comments"
    | "uncompacted_turns"
    | "actor_is_not_author"
    | "has_unfinished_items"
    | "timers_stopped"
    | "approvals_clear"
    | "dependencies_clear";
export type TNumericConditionKind = Extract<
    TConditionKind,
    "stage_residency_seconds" | "uncompacted_characters" | "uncompacted_comments" | "uncompacted_turns"
>;
export type TWorkflowCondition =
    | { kind: TNumericConditionKind; operator: "gte"; value: number }
    | { kind: Exclude<TConditionKind, TNumericConditionKind>; operator: "eq"; value: boolean };
export type TActionKind = "bot.invoke" | "comments.compact" | "card.archive" | "checkitems.complete" | "timers.stop" | "author.notify";
export type TWorkflowAction = { uid?: string; requires_previous_success: boolean } & (
    | { kind: "bot.invoke"; executor: { kind: "bot"; uid: string }; config: { bot_uid: string; schedule_uid?: string } }
    | { kind: "comments.compact"; executor: { kind: "compactor" }; config: Record<string, never> }
    | { kind: "card.archive"; executor: { kind: "compactor" }; config: { archive_after_seconds: number } }
    | { kind: "checkitems.complete" | "timers.stop" | "author.notify"; executor: { kind: "event_actor" }; config: Record<string, never> }
);
export interface IWorkflowRule {
    uid?: string;
    revision?: number;
    name: string;
    is_enabled: boolean;
    trigger: TWorkflowTrigger;
    condition_match: "all" | "any";
    conditions: TWorkflowCondition[];
    actions: TWorkflowAction[];
}
export interface IWorkflowBotOptions {
    bots: { uid: string; name: string; entry_available: boolean; policy_available: boolean }[];
    schedules: { uid: string; bot_uid: string; card_uid: string; name: string; is_available: boolean }[];
}
export const workflowRuleWrite = ({ revision: _, ...rule }: IWorkflowRule) => rule;
export interface IWorkflowStage {
    revision: string;
    uid: string;
    used_column_count?: number;
    key: string;
    name: string;
    description: string;
    color: string;
    order: number;
    is_builtin: boolean;
    is_active: boolean;
    counts_as_completed: boolean;
    active_queue_policy: "include" | "exclude" | "conditional";
    overdue_policy: "normal" | "suppress";
    confirm_descendant_move: boolean;
    rules: IWorkflowRule[];
    translations: Record<string, { name: string; description: string }>;
}
export type TWorkflowInput = Omit<IWorkflowStage, "uid" | "is_builtin" | "is_active" | "used_column_count" | "revision"> & {
    uid?: string;
    expected_revision?: string;
};
export const useWorkflowStages = () => {
    const { mutate, query } = useQueryMutation();
    const load = query(["workflow-stages"], async () => (await api.get<{ stages: IWorkflowStage[] }>("/settings/workflow-stages")).data.stages, {
        retry: 0,
        staleTime: 30000,
    });
    const save = mutate(
        ["save-workflow-stage"],
        async ({ uid, ...input }: TWorkflowInput) => {
            const form = { ...input, rules: input.rules.map(workflowRuleWrite) };
            const response = uid
                ? await api.put<{ stage: IWorkflowStage }>(`/settings/workflow-stages/${uid}`, form)
                : await api.post<{ stage: IWorkflowStage }>("/settings/workflow-stages", form);
            return response.data.stage;
        },
        { retry: 0 }
    );
    const deactivate = mutate(
        ["deactivate-workflow-stage"],
        async ({ uid, expected_revision }: { uid: string; expected_revision?: string }) =>
            (await api.post<{ stage: IWorkflowStage }>(`/settings/workflow-stages/${uid}/deactivate`, { expected_revision })).data.stage,
        { retry: 0 }
    );
    return { load, save, deactivate };
};
