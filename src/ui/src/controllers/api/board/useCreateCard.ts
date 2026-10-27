import type { IEditorContent } from "@/core/models/Base";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";

export interface ICreateCardForm {
    project_uid: string;
    project_column_uid: string;
    title: string;
    description?: IEditorContent;
    assign_users?: string[];
    next_action?: string | null;
}

const useCreateCard = (options?: TMutationOptions<ICreateCardForm, { uid: string }>) => {
    const { mutate } = useQueryMutation();

    const createCard = async (params: ICreateCardForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.CREATE, {
            uid: params.project_uid,
        });
        const res = await api.post(
            url,
            {
                project_column_uid: params.project_column_uid,
                title: params.title,
                ...(params.description ? { description: params.description } : {}),
                assign_users: params.assign_users,
                next_action: params.next_action,
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        return {
            uid: res.data.card.uid,
        };
    };

    const result = mutate(["create-card"], createCard, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateCard;
