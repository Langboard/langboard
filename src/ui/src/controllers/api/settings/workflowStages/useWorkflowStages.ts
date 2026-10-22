import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";

export interface IWorkflowBotBinding {
    bot_uid: string;
    condition: "card_moved" | "bot_cron_scheduled";
    schedule_uid?: string;
    interval_seconds?: number;
}
export interface IWorkflowBotOptions {
    bots: { uid: string; name: string; entry_available: boolean }[];
    schedules: { uid: string; bot_uid: string; card_uid: string; name: string; is_available: boolean }[];
}
export const CONTINUOUS_EFFECT_DEFAULTS = {
    bots: [] as IWorkflowBotBinding[],
    auto_compact: false,
    auto_archive: false,
    interval_seconds: 300,
    archive_after_seconds: 86400,
    compact_after_characters: 12000,
    compact_after_comments: 0,
    compact_after_turns: 0,
};
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
    entry_effects: string[];
    entry_bots?: IWorkflowBotBinding[];
    continuous_effects?: Partial<typeof CONTINUOUS_EFFECT_DEFAULTS>;
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
        async ({ uid, ...form }: TWorkflowInput) => {
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
