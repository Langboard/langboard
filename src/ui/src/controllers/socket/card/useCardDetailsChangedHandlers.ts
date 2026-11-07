import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectCard } from "@/core/models";
import type { IContentBlock, IWorkState } from "@/core/models/ProjectCard";
import { IEditorContent } from "@/core/models/Base";
import { ESocketTopic } from "@langboard/core/enums";
import { refreshCardChangeState } from "@/controllers/api/board/refreshCardChangeState";
import type { TCardPriority } from "@langboard/core/constants";

export interface ICardDetailsChangedRawResponse {
    priority?: TCardPriority;
    read_state_changed?: boolean;
    change_count_changed?: boolean;
    can_cross_board?: boolean;
    updated_at?: string;
    title?: string;
    next_action?: string | null;
    description?: IEditorContent;
    content_blocks?: IContentBlock[];
    description_content_source?: "blocks" | "description";
    deadline_at?: string | null;
    work_state?: IWorkState;
}

export interface IUseCardDetailsChangedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
    cardUID: string;
}

const useCardDetailsChangedHandlers = ({ callback, projectUID, cardUID }: IUseCardDetailsChangedHandlersProps) => {
    return useSocketHandler<{}, ICardDetailsChangedRawResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-card-details-changed-${cardUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.CARD.DETAILS_CHANGED,
            params: { uid: cardUID },
            callback,
            responseConverter: (data) => {
                const card = ProjectCard.Model.getModel(cardUID);
                if (card?.project_uid === projectUID) {
                    if (data.read_state_changed) card.read_state_version = (card.read_state_version ?? 0) + 1;
                    if (data.change_count_changed || data.read_state_changed) void refreshCardChangeState(projectUID, cardUID);
                    if (data.can_cross_board !== undefined) card.can_cross_board = data.can_cross_board;
                    if (data.next_action !== undefined) card.next_action = data.next_action;
                    if (data.title !== undefined) card.title = data.title;
                    if (data.priority !== undefined) card.priority = data.priority;
                    card.applyDescriptionSnapshot({
                        description: data.description,
                        content_blocks: data.content_blocks,
                        description_content_source: data.description_content_source,
                    });
                    if (data.deadline_at !== undefined) card.deadline_at = data.deadline_at ?? undefined;
                    if (data.work_state !== undefined) card.work_state = data.work_state;
                    if (data.updated_at !== undefined) card.updated_at = data.updated_at;
                }
                return {};
            },
        },
    });
};

export default useCardDetailsChangedHandlers;
