import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { GraphApprovalRequestModel } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardGraphApprovalRequestedRawResponse {
    approval: GraphApprovalRequestModel.Interface;
}

export interface IUseBoardGraphApprovalRequestedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardGraphApprovalRequestedHandlers = ({ callback, projectUID }: IUseBoardGraphApprovalRequestedHandlersProps) => {
    return useSocketHandler<{}, IBoardGraphApprovalRequestedRawResponse>({
        topic: ESocketTopic.BoardSettings,
        topicId: projectUID,
        eventKey: `board-graph-approval-requested-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.GRAPH_APPROVAL.REQUESTED,
            callback,
            responseConverter: (data) => {
                GraphApprovalRequestModel.Model.fromOne(data.approval, true);
                return {};
            },
        },
    });
};

export default useBoardGraphApprovalRequestedHandlers;
