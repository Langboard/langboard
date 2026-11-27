import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ApiComfortToolModel } from "@/core/models";

export interface ICreateApiComfortToolForm {
    name?: string;
    label: string;
    description: string;
    api_names: string[];
}

const useCreateApiComfortTool = (options?: TMutationOptions<ICreateApiComfortToolForm>) => {
    const { mutate } = useQueryMutation();

    const createApiComfortTool = async (params: ICreateApiComfortToolForm) => {
        const res = await api.post(Routing.API.SETTINGS.SCHEMAS.API_COMFORT_TOOLS, params, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        ApiComfortToolModel.Model.fromArray(res.data.api_comfort_tools ?? [], true);
    };

    const result = mutate(["create-api-comfort-tool"], createApiComfortTool, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateApiComfortTool;
