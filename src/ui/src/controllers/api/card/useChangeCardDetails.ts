import { sanitizeEditorValue } from "@/components/Editor/utils";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import type { IEditorContent } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";

interface IBaseChangeCardDetailsForm {
    project_uid: string;
    card_uid: string;
}

interface IDetails {
    title?: string;
    description?: IEditorContent;
    deadline_at?: Date | "";
}

export type TChangeCardDetailsForm = IBaseChangeCardDetailsForm & Partial<IDetails>;

const useChangeCardDetails = (options?: TMutationOptions<TChangeCardDetailsForm>) => {
    const { mutate } = useQueryMutation();

    const changeCardDetails = async (params: TChangeCardDetailsForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.CHANGE_DETAILS, {
            uid: params.project_uid,
            card_uid: params.card_uid,
        });
        const details: Partial<IDetails> = {};

        if ("title" in params) {
            details.title = params.title;
        }
        if ("description" in params) {
            details.description = params.description ? sanitizeEditorValue(params.description) : params.description;
        }
        if ("deadline_at" in params) {
            details.deadline_at = params.deadline_at;
        }

        const res = await api.put(url, details, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        return res.data;
    };

    const result = mutate(["change-card-details"], changeCardDetails, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useChangeCardDetails;
