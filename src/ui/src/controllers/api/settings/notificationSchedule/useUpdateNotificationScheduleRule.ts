import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { NotificationScheduleRuleModel } from "@/core/models";
import { IBaseModel } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";

export interface IUpdateNotificationScheduleRuleForm extends Partial<
    Omit<NotificationScheduleRuleModel.Interface, keyof IBaseModel | "last_run_at">
> {
    timezone?: string | number;
}

const useUpdateNotificationScheduleRule = (
    rule: NotificationScheduleRuleModel.TModel,
    options?: TMutationOptions<IUpdateNotificationScheduleRuleForm>
) => {
    const { mutate } = useQueryMutation();

    const updateNotificationScheduleRule = async (params: IUpdateNotificationScheduleRuleForm) => {
        const res = await api.put(Utils.String.format(Routing.API.SETTINGS.NOTIFICATION_SCHEDULE_RULE.UPDATE, { rule_uid: rule.uid }), params, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        NotificationScheduleRuleModel.Model.fromOne(res.data.notification_rule, true);

        return res.data;
    };

    const result = mutate(["update-notification-schedule-rule"], updateNotificationScheduleRule, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useUpdateNotificationScheduleRule;
