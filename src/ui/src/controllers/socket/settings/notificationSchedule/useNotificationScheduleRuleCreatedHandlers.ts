import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { NotificationScheduleRuleModel } from "@/core/models";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";

export interface INotificationScheduleRuleCreatedRawResponse {
    notification_rule: NotificationScheduleRuleModel.Interface;
}

const useNotificationScheduleRuleCreatedHandlers = ({ callback }: IBaseUseSocketHandlersProps<{}>) => {
    return useSocketHandler<{}, INotificationScheduleRuleCreatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.NotificationSchedule,
        eventKey: "notification-schedule-rule-created",
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.NOTIFICATION_SCHEDULE.RULE.CREATED,
            callback,
            responseConverter: (data) => {
                NotificationScheduleRuleModel.Model.fromOne(data.notification_rule, true);
                return {};
            },
        },
    });
};

export default useNotificationScheduleRuleCreatedHandlers;
