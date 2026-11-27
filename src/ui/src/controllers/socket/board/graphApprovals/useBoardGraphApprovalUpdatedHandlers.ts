import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { GraphApprovalRequestModel } from "@/core/models";
import { EGraphApprovalStatus } from "@/core/models/GraphApprovalRequestModel";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardGraphApprovalUpdatedRawResponse {
    approval: GraphApprovalRequestModel.Interface;
}

export interface IUseBoardGraphApprovalUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardGraphApprovalUpdatedHandlers = ({ callback, projectUID }: IUseBoardGraphApprovalUpdatedHandlersProps) => {
    return useSocketHandler<{}, IBoardGraphApprovalUpdatedRawResponse>({
        topic: ESocketTopic.BoardSettings,
        topicId: projectUID,
        eventKey: `board-graph-approval-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.GRAPH_APPROVAL.UPDATED,
            callback,
            responseConverter: (data) => {
                if (data.approval.status === EGraphApprovalStatus.Pending) {
                    GraphApprovalRequestModel.Model.fromOne(data.approval, true);
                } else {
                    GraphApprovalRequestModel.Model.deleteModel(data.approval.uid);
                }
                return {};
            },
        },
    });
};

export default useBoardGraphApprovalUpdatedHandlers;
