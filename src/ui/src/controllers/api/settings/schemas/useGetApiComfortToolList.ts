import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ApiComfortToolModel } from "@/core/models";

const useGetApiComfortToolList = (options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();

    const getApiComfortToolList = async () => {
        const res = await api.get(Routing.API.SETTINGS.SCHEMAS.API_COMFORT_TOOLS, {
            env: {
                noToast: options?.interceptToast,
            } as never,
        });

        ApiComfortToolModel.Model.fromArray(res.data.api_comfort_tools ?? [], true);

        return {};
    };

    const result = mutate(["get-api-comfort-tool-list"], getApiComfortToolList, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useGetApiComfortToolList;
