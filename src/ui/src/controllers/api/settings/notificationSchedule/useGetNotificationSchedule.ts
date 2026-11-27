import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { NotificationScheduleRuleModel } from "@/core/models";
import { subscribeModelSocketTopic } from "@/core/models/base/socketSubscriptions";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";

export interface INotificationScheduleResponse {
    notification_rule_schema: NotificationScheduleRuleModel.IRuleSchema;
}

const useGetNotificationSchedule = (options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();

    const getNotificationSchedule = async () => {
        const res = await api.get(Routing.API.SETTINGS.NOTIFICATION_SCHEDULE, {
            env: {
                noToast: options?.interceptToast,
            } as never,
        });

        NotificationScheduleRuleModel.Model.fromArray(res.data.notification_rules, true);
        subscribeModelSocketTopic(ESocketTopic.AppSettings, [ESettingSocketTopicID.NotificationSchedule]);

        return {
            notification_rule_schema: res.data.notification_rule_schema,
        } satisfies INotificationScheduleResponse;
    };

    const result = mutate(["get-notification-schedule"], getNotificationSchedule, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useGetNotificationSchedule;
