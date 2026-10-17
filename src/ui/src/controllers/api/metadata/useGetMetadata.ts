import { TMetadataForm } from "@/controllers/api/metadata/types";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TQueryOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { MetadataModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import type { QueryFunctionContext } from "@tanstack/react-query";

const useGetMetadata = (form: TMetadataForm, options?: TQueryOptions) => {
    const { query } = useQueryMutation();

    let url: string;
    switch (form.type) {
        case "card":
            url = Utils.String.format(Routing.API.METADATA.CARD, { uid: form.project_uid, card_uid: form.uid });
            break;
        case "project_wiki":
            url = Utils.String.format(Routing.API.METADATA.PROJECT_WIKI, { uid: form.project_uid, wiki_uid: form.uid });
            break;
        default:
            throw new Error("Invalid metadata type");
    }

    const getMetadata = async ({ signal }: QueryFunctionContext) => {
        const res = await api.get(url, {
            signal,
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        const model: MetadataModel.Interface = {
            uid: form.uid,
            type: form.type,
            metadata: res.data.metadata,
            created_at: new Date(),
            updated_at: new Date(),
        };

        MetadataModel.Model.fromOne(model, true);

        return res.data;
    };

    const result = query(["get-metadata", form.type, form.project_uid, form.uid], getMetadata, {
        ...options,
        retry: 0,
        refetchInterval: Infinity,
        refetchOnWindowFocus: false,
    });

    return result;
};

export default useGetMetadata;
