import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ProjectColumn } from "@/core/models";
import { Utils } from "@langboard/core/utils";

export interface IChangeProjectColumnWorkflowStageForm {
    project_uid: string;
    project_column_uid: string;
    workflow_stage: ProjectColumn.Interface["workflow_stage"];
}

export interface IChangeProjectColumnWorkflowStageResponse {
    workflow_stage: ProjectColumn.Interface["workflow_stage"];
    workflow_counts_as_completed: boolean | null;
}

const useChangeProjectColumnWorkflowStage = (
    options?: TMutationOptions<IChangeProjectColumnWorkflowStageForm, IChangeProjectColumnWorkflowStageResponse>
) => {
    const { mutate } = useQueryMutation();
    return mutate(
        ["change-project-column-workflow-stage"],
        async (params: IChangeProjectColumnWorkflowStageForm) => {
            const url = Utils.String.format(Routing.API.BOARD.COLUMN.CHANGE_WORKFLOW_STAGE, {
                uid: params.project_uid,
                project_column_uid: params.project_column_uid,
            });
            const response = await api.put<IChangeProjectColumnWorkflowStageResponse>(url, {
                workflow_stage: params.workflow_stage,
            });
            ProjectColumn.Model.getModel(params.project_column_uid)?.applyWorkflowContext(response.data);
            return response.data;
        },
        { ...options, retry: 0 }
    );
};

export default useChangeProjectColumnWorkflowStage;
