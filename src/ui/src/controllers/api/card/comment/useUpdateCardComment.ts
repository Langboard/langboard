import { sanitizeEditorValue } from "@/components/Editor/utils";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { IEditorContent } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";

export interface IUpdateCardCommentForm {
    project_uid: string;
    card_uid: string;
    comment_uid: string;
    content: IEditorContent;
}

const useUpdateCardComment = (options?: TMutationOptions<IUpdateCardCommentForm>) => {
    const { mutate } = useQueryMutation();

    const updateCardComment = async (params: IUpdateCardCommentForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.COMMENT.UPDATE, {
            uid: params.project_uid,
            card_uid: params.card_uid,
            comment_uid: params.comment_uid,
        });
        const res = await api.put(
            url,
            {
                ...sanitizeEditorValue(params.content),
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        res.data.updated_at = new Date(res.data.updated_at);

        return res.data;
    };

    const result = mutate(["update-card-comment"], updateCardComment, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useUpdateCardComment;
