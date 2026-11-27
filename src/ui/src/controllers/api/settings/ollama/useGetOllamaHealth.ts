import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";

export interface IGetOllamaHealthResponse {
    configured: bool;
    available: bool;
}

const useGetOllamaHealth = (options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();

    const getOllamaHealth = async () => {
        const res = await api.get<IGetOllamaHealthResponse>(Routing.API.SETTINGS.OLLAMA.HEALTH, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        return res.data;
    };

    const result = mutate(["get-ollama-health"], getOllamaHealth, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useGetOllamaHealth;
