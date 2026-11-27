import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { NotificationScheduleRuleModel } from "@/core/models";

export interface IDeleteSelectedNotificationScheduleRulesForm {
    rule_uids: string[];
}

const useDeleteSelectedNotificationScheduleRules = (options?: TMutationOptions<IDeleteSelectedNotificationScheduleRulesForm>) => {
    const { mutate } = useQueryMutation();

    const deleteSelectedNotificationScheduleRules = async (params: IDeleteSelectedNotificationScheduleRulesForm) => {
        const res = await api.delete(Routing.API.SETTINGS.NOTIFICATION_SCHEDULE_RULE.DELETE_SELECTED, {
            data: params,
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        NotificationScheduleRuleModel.Model.deleteModels(params.rule_uids);

        return res.data;
    };

    const result = mutate(["delete-selected-notification-schedule-rules"], deleteSelectedNotificationScheduleRules, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useDeleteSelectedNotificationScheduleRules;
