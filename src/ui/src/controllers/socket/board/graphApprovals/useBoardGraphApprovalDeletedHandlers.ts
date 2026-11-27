import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { GraphApprovalRequestModel } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardGraphApprovalDeletedRawResponse {
    uid: string;
}

export interface IUseBoardGraphApprovalDeletedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardGraphApprovalDeletedHandlers = ({ callback, projectUID }: IUseBoardGraphApprovalDeletedHandlersProps) => {
    return useSocketHandler<{}, IBoardGraphApprovalDeletedRawResponse>({
        topic: ESocketTopic.BoardSettings,
        topicId: projectUID,
        eventKey: `board-graph-approval-deleted-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.GRAPH_APPROVAL.DELETED,
            callback,
            responseConverter: (data) => {
                GraphApprovalRequestModel.Model.deleteModel(data.uid);
                return {};
            },
        },
    });
};

export default useBoardGraphApprovalDeletedHandlers;
