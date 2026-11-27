import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler from "@/core/helpers/SocketHandler";
import { NotificationScheduleRuleModel } from "@/core/models";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";

export interface IUseNotificationScheduleRuleDeletedHandlersProps {
    rule: NotificationScheduleRuleModel.TModel;
}

const useNotificationScheduleRuleDeletedHandlers = ({ rule }: IUseNotificationScheduleRuleDeletedHandlersProps) => {
    return useSocketHandler<{}, {}>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.NotificationSchedule,
        eventKey: `notification-schedule-rule-deleted-${rule.uid}`,
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.NOTIFICATION_SCHEDULE.RULE.DELETED,
            params: { uid: rule.uid },
            responseConverter: () => {
                NotificationScheduleRuleModel.Model.deleteModel(rule.uid);
                return {};
            },
        },
    });
};

export default useNotificationScheduleRuleDeletedHandlers;
