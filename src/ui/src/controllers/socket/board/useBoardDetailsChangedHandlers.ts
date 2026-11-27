import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { Project } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardDetailsChangedResponse {
    title?: string;
    description?: string;
    ai_description?: string;
    project_type?: string;
    archive_visible_days?: number;
}

export interface IUseBoardDetailsChangedHandlersProps extends IBaseUseSocketHandlersProps<IBoardDetailsChangedResponse> {
    projectUID: string;
}

const useBoardDetailsChangedHandlers = ({ callback, projectUID }: IUseBoardDetailsChangedHandlersProps) => {
    return useSocketHandler<IBoardDetailsChangedResponse, IBoardDetailsChangedResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-details-changed-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.DETAILS_CHANGED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                const project = Project.Model.getModel(projectUID);
                if (project) {
                    Object.entries(data).forEach(([key, value]) => {
                        project[key] = value as unknown as never;
                    });
                }
                return data;
            },
        },
    });
};

export default useBoardDetailsChangedHandlers;
