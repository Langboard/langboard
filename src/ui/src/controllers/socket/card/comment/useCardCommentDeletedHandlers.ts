import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectCard, ProjectCardComment } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import { ESocketTopic } from "@langboard/core/enums";

export interface ICardCommentDeletedRawResponse {
    comment_uid: string;
}

export interface IUseCardCommentDeletedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
    cardUID: string;
}

const useCardCommentDeletedHandlers = ({ callback, projectUID, cardUID }: IUseCardCommentDeletedHandlersProps) => {
    return useSocketHandler<{}, ICardCommentDeletedRawResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-card-comment-deleted-${cardUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.CARD.COMMENT.DELETED,
            params: { uid: cardUID },
            callback,
            responseConverter: (data) => {
                const card = ProjectCard.Model.getModel(cardUID);
                if (card && Utils.Type.isNumber(card.count_comment)) {
                    card.count_comment = Math.max(card.count_comment - 1, 0);
                }
                ProjectCardComment.Model.deleteModel(data.comment_uid);
                return {};
            },
        },
    });
};

export default useCardCommentDeletedHandlers;
