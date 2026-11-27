import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";
import { Project } from "@/core/models";

interface IToggleStarProjectForm {
    uid: string;
}

const useToggleStarProject = (options?: TMutationOptions<IToggleStarProjectForm>) => {
    const { mutate } = useQueryMutation();

    const toggleStarProject = async (params: IToggleStarProjectForm) => {
        const url = Utils.String.format(Routing.API.DASHBOARD.TOGGLE_STAR_PROJECT, {
            uid: params.uid,
        });

        const res = await api.put(url, params, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        const project = Project.Model.getModel(params.uid);
        if (project) {
            project.starred = !project.starred;
        }

        return res.data;
    };

    const result = mutate(["toggle-star-project"], toggleStarProject, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useToggleStarProject;
