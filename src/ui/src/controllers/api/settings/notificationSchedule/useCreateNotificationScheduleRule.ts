import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { NotificationScheduleRuleModel } from "@/core/models";
import { IBaseModel } from "@/core/models/Base";

export interface ICreateNotificationScheduleRuleForm extends Omit<NotificationScheduleRuleModel.Interface, keyof IBaseModel | "last_run_at"> {
    timezone?: string | number;
}

const useCreateNotificationScheduleRule = (options?: TMutationOptions<ICreateNotificationScheduleRuleForm>) => {
    const { mutate } = useQueryMutation();

    const createNotificationScheduleRule = async (params: ICreateNotificationScheduleRuleForm) => {
        const res = await api.post(Routing.API.SETTINGS.NOTIFICATION_SCHEDULE_RULE.CREATE, params, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        NotificationScheduleRuleModel.Model.fromOne(res.data.notification_rule, true);

        return {};
    };

    const result = mutate(["create-notification-schedule-rule"], createNotificationScheduleRule, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateNotificationScheduleRule;
