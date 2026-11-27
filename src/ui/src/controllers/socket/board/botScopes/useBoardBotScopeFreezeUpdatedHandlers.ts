import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { TBotRelatedTargetTable } from "@/core/models/types/bot.related.type";
import { ESocketTopic } from "@langboard/core/enums";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";

export interface IBoardBotScopeFreezeUpdatedRawResponse {
    scope_table: TBotRelatedTargetTable;
    uid: string;
    is_frozen: bool;
}

export interface IUseBoardBotScopeFreezeUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardBotScopeFreezeUpdatedHandlers = ({ callback, projectUID }: IUseBoardBotScopeFreezeUpdatedHandlersProps) => {
    return useSocketHandler<{}, IBoardBotScopeFreezeUpdatedRawResponse>({
        topic: ESocketTopic.BoardSettings,
        topicId: projectUID,
        eventKey: `board-bot-scope-freeze-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.BOT.SCOPE.FREEZE_UPDATED,
            callback,
            responseConverter: (data) => {
                const targetModel = BOT_SCOPES[data.scope_table];
                const model = targetModel?.Model.getModel(data.uid);
                if (model) {
                    model.is_frozen = data.is_frozen;
                }

                return {};
            },
        },
    });
};

export default useBoardBotScopeFreezeUpdatedHandlers;
