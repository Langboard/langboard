import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { TBotRelatedTargetTable } from "@/core/models/types/bot.related.type";
import { DomainEventType } from "@/core/events/DomainEventType";
import { ESocketTopic } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";

export interface IBoardBotScopeConditionsUpdatedRawResponse {
    scope_table: TBotRelatedTargetTable;
    uid: string;
    conditions: string[];
    default_scope_branch_uid?: string;
}

export interface IUseBoardBotScopeConditionsUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardBotScopeConditionsUpdatedHandlers = ({ callback, projectUID }: IUseBoardBotScopeConditionsUpdatedHandlersProps) => {
    return useSocketHandler<{}, IBoardBotScopeConditionsUpdatedRawResponse>({
        topic: ESocketTopic.BoardSettings,
        topicId: projectUID,
        eventKey: `board-bot-scope-trigger-conditions-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.BOT.SCOPE.TRIGGER_CONDITION_TOGGLED,
            callback,
            responseConverter: (data) => {
                let model;
                const targetModel = BOT_SCOPES[data.scope_table];
                if (targetModel) {
                    model = targetModel.Model.getModel(data.uid);
                }

                if (model) {
                    model.conditions = data.conditions.map((condition) => {
                        return Utils.String.convertSafeEnum(DomainEventType, condition);
                    });

                    model.default_scope_branch_uid = data.default_scope_branch_uid;
                }

                return {};
            },
        },
    });
};

export default useBoardBotScopeConditionsUpdatedHandlers;
