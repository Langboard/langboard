import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";
import type { ProjectIdentity } from "@/core/models/projectIdentity";

interface IChangeProjectDetailsForm {
    title: string;
    identity?: ProjectIdentity | null;
    description?: string;
    project_type: string;
    archive_visible_days: number;
}

interface IChangeProjectDetailsResponse {
    identity?: ProjectIdentity | null;
}

const useChangeProjectDetails = (projectUID: string, options?: TMutationOptions<IChangeProjectDetailsForm, IChangeProjectDetailsResponse>) => {
    const { mutate } = useQueryMutation();

    const changeProjectDetails = async (params: IChangeProjectDetailsForm): Promise<IChangeProjectDetailsResponse> => {
        const url = Utils.String.format(Routing.API.BOARD.SETTINGS.UPDATE_DETAILS, { uid: projectUID });
        const res = await api.put(url, params, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        return res.data;
    };

    const result = mutate(["change-project-details"], changeProjectDetails, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useChangeProjectDetails;
