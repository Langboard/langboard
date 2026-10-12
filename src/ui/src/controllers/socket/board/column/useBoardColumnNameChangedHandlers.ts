import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectColumn } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardColumnNameChangedRawResponse {
    uid: string;
    name: string;
    description?: string;
    translations?: Record<string, { name?: string; description?: string }>;
    workflow_stage?: string | null;
}

export interface IUseBoardColumnNameChangedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardColumnNameChangedHandlers = ({ callback, projectUID }: IUseBoardColumnNameChangedHandlersProps) => {
    return useSocketHandler<{}, IBoardColumnNameChangedRawResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-column-name-changed-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.COLUMN.NAME_CHANGED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                const column = ProjectColumn.Model.getModel(data.uid);
                if (column) {
                    column.applyMetadata(data);
                }
                return {};
            },
        },
    });
};

export default useBoardColumnNameChangedHandlers;
