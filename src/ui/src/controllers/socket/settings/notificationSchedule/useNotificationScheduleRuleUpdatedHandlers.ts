import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { NotificationScheduleRuleModel } from "@/core/models";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";

export interface INotificationScheduleRuleUpdatedRawResponse {
    notification_rule: NotificationScheduleRuleModel.Interface;
}

export interface IUseNotificationScheduleRuleUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    rule: NotificationScheduleRuleModel.TModel;
}

const useNotificationScheduleRuleUpdatedHandlers = ({ callback, rule }: IUseNotificationScheduleRuleUpdatedHandlersProps) => {
    return useSocketHandler<{}, INotificationScheduleRuleUpdatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.NotificationSchedule,
        eventKey: `notification-schedule-rule-updated-${rule.uid}`,
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.NOTIFICATION_SCHEDULE.RULE.UPDATED,
            params: { uid: rule.uid },
            callback,
            responseConverter: (data) => {
                NotificationScheduleRuleModel.Model.fromOne(data.notification_rule, true);
                return {};
            },
        },
    });
};

export default useNotificationScheduleRuleUpdatedHandlers;
