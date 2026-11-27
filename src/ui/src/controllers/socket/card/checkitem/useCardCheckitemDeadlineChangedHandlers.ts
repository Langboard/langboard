import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectCheckitem } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface ICardCheckitemDeadlineChangedRawResponse {
    deadline_at?: Date | null;
}

export interface IUseCardCheckitemDeadlineChangedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    cardUID: string;
    checkitem: ProjectCheckitem.TModel;
}

const useCardCheckitemDeadlineChangedHandlers = ({ callback, cardUID, checkitem }: IUseCardCheckitemDeadlineChangedHandlersProps) => {
    return useSocketHandler<{}, ICardCheckitemDeadlineChangedRawResponse>({
        topic: ESocketTopic.BoardCard,
        topicId: cardUID,
        eventKey: `board-card-checkitem-deadline-changed-${checkitem.uid}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.CARD.CHECKITEM.DEADLINE_CHANGED,
            params: { uid: checkitem.uid },
            callback,
            responseConverter: (data) => {
                checkitem.deadline_at = data.deadline_at ? new Date(data.deadline_at) : undefined;
                return {};
            },
        },
    });
};

export default useCardCheckitemDeadlineChangedHandlers;
