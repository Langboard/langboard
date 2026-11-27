import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { GraphApprovalRequestModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

export interface IApproveGraphApprovalForm {
    project_uid: string;
    approval_uid: string;
}

export interface IApproveGraphApprovalResponse {
    approval: GraphApprovalRequestModel.TModel;
}

const useApproveGraphApproval = (options?: TMutationOptions<IApproveGraphApprovalForm, IApproveGraphApprovalResponse>) => {
    const { mutate } = useQueryMutation();

    const approveGraphApproval = async (form: IApproveGraphApprovalForm): Promise<IApproveGraphApprovalResponse> => {
        const url = Utils.String.format(Routing.API.BOARD.GRAPH_APPROVAL.APPROVE, {
            uid: form.project_uid,
            approval_uid: form.approval_uid,
        });
        const res = await api.post(url, undefined, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        const approval = GraphApprovalRequestModel.Model.fromOne(res.data.approval, true);

        return {
            approval,
        };
    };

    return mutate(["approve-graph-approval"], approveGraphApproval, {
        ...options,
    });
};

export default useApproveGraphApproval;
