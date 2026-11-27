import { sanitizeEditorValue } from "@/components/Editor/utils";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { IEditorContent } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";

interface IBaseChangeWikiDetailsForm {
    project_uid: string;
    wiki_uid: string;
}

interface IDetails {
    title?: string;
    content?: IEditorContent;
}

type TChangeableDetail = keyof IDetails;

export type TChangeWikiDetailsForm<TDetail extends TChangeableDetail> = IBaseChangeWikiDetailsForm & Pick<IDetails, TDetail>;

const useChangeWikiDetails = <TDetail extends TChangeableDetail>(type: TDetail, options?: TMutationOptions<TChangeWikiDetailsForm<TDetail>>) => {
    const { mutate } = useQueryMutation();

    const changeWikiDetails = async (params: TChangeWikiDetailsForm<TDetail>) => {
        const url = Utils.String.format(Routing.API.BOARD.WIKI.CHANGE_DETAILS, {
            uid: params.project_uid,
            wiki_uid: params.wiki_uid,
        });
        const value = type === "content" && params[type] ? sanitizeEditorValue(params[type] as IEditorContent) : params[type];
        const res = await api.put(
            url,
            {
                [type]: value,
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        return res.data;
    };

    const result = mutate(["change-wiki-details"], changeWikiDetails, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useChangeWikiDetails;
