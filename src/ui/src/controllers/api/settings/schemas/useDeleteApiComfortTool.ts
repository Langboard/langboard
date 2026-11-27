import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ApiComfortToolModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

const useDeleteApiComfortTool = (comfortTool: ApiComfortToolModel.TModel, options?: TMutationOptions<unknown>) => {
    const { mutate } = useQueryMutation();

    const deleteApiComfortTool = async () => {
        const url = Utils.String.format(Routing.API.SETTINGS.SCHEMAS.API_COMFORT_TOOL, { comfort_tool_name: comfortTool.name });
        const res = await api.delete(url, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        ApiComfortToolModel.Model.deleteModel(comfortTool.uid);

        return res.data;
    };

    const result = mutate(["delete-api-comfort-tool"], deleteApiComfortTool, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useDeleteApiComfortTool;
