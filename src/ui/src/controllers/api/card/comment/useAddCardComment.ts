import { sanitizeEditorValue } from "@/components/Editor/utils";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { IEditorContent } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";
import type { ICardCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
import useAuthStore from "@/core/stores/AuthStore";

export interface IAddCardCommentForm {
    project_uid: string;
    card_uid: string;
    content: IEditorContent;
    anchor?: ICardCommentAnchor | null;
    request_uid?: string;
}

const useAddCardComment = (options?: TMutationOptions<IAddCardCommentForm>) => {
    const { mutate, queryClient } = useQueryMutation();

    const addCardComment = async (params: IAddCardCommentForm) => {
        const actor = useAuthStore.getState().currentUser?.uid;
        const session = useAuthStore.getState().getSessionVersion();
        const url = Utils.String.format(Routing.API.BOARD.CARD.COMMENT.ADD, { uid: params.project_uid, card_uid: params.card_uid });
        const res = await api.post(
            url,
            {
                ...sanitizeEditorValue(params.content),
                anchor: params.anchor ?? null,
                ...(params.request_uid ? { request_uid: params.request_uid } : {}),
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        const current = useAuthStore.getState();
        if (actor && current.currentUser?.uid === actor && current.getSessionVersion() === session) {
            await queryClient.invalidateQueries({
                queryKey: [`get-card-comments-${params.project_uid}-${params.card_uid}`],
                exact: true,
            });
        }
        return res.data;
    };

    const result = mutate(["add-card-comment"], addCardComment, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useAddCardComment;
