import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ESocketTopic } from "@langboard/core/enums";
import syncCardRelationships, { ICardRelationshipsUpdatedRawResponse } from "@/controllers/socket/card/syncCardRelationships";

export interface IUseCardRelationshipsUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useCardRelationshipsUpdatedHandlers = ({ callback, projectUID }: IUseCardRelationshipsUpdatedHandlersProps) => {
    return useSocketHandler<{}, ICardRelationshipsUpdatedRawResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-card-relationships-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.CARD.RELATIONSHIPS_UPDATED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                syncCardRelationships(data);
                return {};
            },
        },
    });
};

export default useCardRelationshipsUpdatedHandlers;
