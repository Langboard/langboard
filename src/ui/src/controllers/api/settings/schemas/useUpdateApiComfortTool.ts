import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ApiComfortToolModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

export interface IUpdateApiComfortToolForm {
    name: string;
    label: string;
    description: string;
    api_names: string[];
}

const useUpdateApiComfortTool = (options?: TMutationOptions<IUpdateApiComfortToolForm & { comfort_tool_name: string }>) => {
    const { mutate } = useQueryMutation();

    const updateApiComfortTool = async ({ comfort_tool_name, ...params }: IUpdateApiComfortToolForm & { comfort_tool_name: string }) => {
        const url = Utils.String.format(Routing.API.SETTINGS.SCHEMAS.API_COMFORT_TOOL, { comfort_tool_name });
        const res = await api.put(url, params, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        ApiComfortToolModel.Model.fromArray(res.data.api_comfort_tools ?? [], true);
    };

    const result = mutate(["update-api-comfort-tool"], updateApiComfortTool, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useUpdateApiComfortTool;
