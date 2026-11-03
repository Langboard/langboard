import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectCard } from "@/core/models";
import type { IWorkState } from "@/core/models/ProjectCard";
import { IEditorContent } from "@/core/models/Base";
import { ESocketTopic } from "@langboard/core/enums";
import { queryClient } from "@/core/helpers/QueryClient";
import useAuthStore from "@/core/stores/AuthStore";
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
                    if (data.change_count_changed === true) {
                        const auth = useAuthStore.getState();
                        if (auth.currentUser) {
                            // Counts are per reader; shared events only invalidate the authorized snapshot.
                            void queryClient.invalidateQueries({
                                queryKey: [`get-cards-${projectUID}`, { project_uid: projectUID }, auth.currentUser.uid, auth.getSessionVersion()],
                                exact: true,
                            });
                        }
                    }
                    if (data.read_state_changed) card.read_state_version = (card.read_state_version ?? 0) + 1;
                    if (data.can_cross_board !== undefined) card.can_cross_board = data.can_cross_board;
                    if (data.next_action !== undefined) card.next_action = data.next_action;
                    if (data.title !== undefined) card.title = data.title;
                    if (data.priority !== undefined) card.priority = data.priority;
                    if (data.description !== undefined) card.description = data.description;
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
