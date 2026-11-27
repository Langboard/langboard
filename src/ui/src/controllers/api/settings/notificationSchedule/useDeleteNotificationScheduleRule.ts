import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { NotificationScheduleRuleModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

const useDeleteNotificationScheduleRule = (rule: NotificationScheduleRuleModel.TModel, options?: TMutationOptions<unknown>) => {
    const { mutate } = useQueryMutation();

    const deleteNotificationScheduleRule = async () => {
        const res = await api.delete(Utils.String.format(Routing.API.SETTINGS.NOTIFICATION_SCHEDULE_RULE.DELETE, { rule_uid: rule.uid }), {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        NotificationScheduleRuleModel.Model.deleteModel(rule.uid);

        return res.data;
    };

    const result = mutate(["delete-notification-schedule-rule"], deleteNotificationScheduleRule, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useDeleteNotificationScheduleRule;
