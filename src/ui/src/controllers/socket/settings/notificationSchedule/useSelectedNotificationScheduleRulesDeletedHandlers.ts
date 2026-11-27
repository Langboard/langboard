import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { NotificationScheduleRuleModel } from "@/core/models";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";

export interface ISelectedNotificationScheduleRulesDeletedRawResponse {
    uids: string[];
}

const useSelectedNotificationScheduleRulesDeletedHandlers = ({ callback }: IBaseUseSocketHandlersProps<{}>) => {
    return useSocketHandler<{}, ISelectedNotificationScheduleRulesDeletedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.NotificationSchedule,
        eventKey: "selected-notification-schedule-rules-deleted",
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.NOTIFICATION_SCHEDULE.RULE.SELECTIONS_DELETED,
            callback,
            responseConverter: (data) => {
                NotificationScheduleRuleModel.Model.deleteModels(data.uids);
                return {};
            },
        },
    });
};

export default useSelectedNotificationScheduleRulesDeletedHandlers;
