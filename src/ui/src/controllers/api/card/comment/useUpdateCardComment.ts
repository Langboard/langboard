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
    expected_revision?: string;
}

export interface IUpdateCardCommentResult {
    updated_at: Date;
    content_revision?: string;
}

const useUpdateCardComment = (options?: TMutationOptions<IUpdateCardCommentForm, IUpdateCardCommentResult>) => {
    const { mutate } = useQueryMutation();

    const updateCardComment = async (params: IUpdateCardCommentForm): Promise<IUpdateCardCommentResult> => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.COMMENT.UPDATE, {
            uid: params.project_uid,
            card_uid: params.card_uid,
            comment_uid: params.comment_uid,
        });
        const res = await api.put(
            url,
            {
                ...sanitizeEditorValue(params.content),
                ...(params.expected_revision ? { expected_revision: params.expected_revision } : {}),
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

    const result = mutate<IUpdateCardCommentForm, IUpdateCardCommentResult>(["update-card-comment"], updateCardComment, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useUpdateCardComment;
