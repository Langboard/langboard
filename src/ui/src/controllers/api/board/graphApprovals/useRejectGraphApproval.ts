import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { GraphApprovalRequestModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

export interface IRejectGraphApprovalForm {
    project_uid: string;
    approval_uid: string;
    reason?: string | null;
}

export interface IRejectGraphApprovalResponse {
    approval: GraphApprovalRequestModel.TModel;
}

const useRejectGraphApproval = (options?: TMutationOptions<IRejectGraphApprovalForm, IRejectGraphApprovalResponse>) => {
    const { mutate } = useQueryMutation();

    const rejectGraphApproval = async (form: IRejectGraphApprovalForm): Promise<IRejectGraphApprovalResponse> => {
        const url = Utils.String.format(Routing.API.BOARD.GRAPH_APPROVAL.REJECT, {
            uid: form.project_uid,
            approval_uid: form.approval_uid,
        });
        const res = await api.post(
            url,
            {
                reason: form.reason ?? "",
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        const approval = GraphApprovalRequestModel.Model.fromOne(res.data.approval, true);

        return {
            approval,
        };
    };

    return mutate(["reject-graph-approval"], rejectGraphApproval, {
        ...options,
    });
};

export default useRejectGraphApproval;
