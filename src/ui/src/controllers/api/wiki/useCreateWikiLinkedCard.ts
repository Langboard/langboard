import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { Utils } from "@langboard/core/utils";

export interface ICreateWikiLinkedCardForm {
    project_uid: string;
    wiki_uid: string;
    column_uid?: string;
}

export interface ICreateWikiLinkedCardResponse {
    card: ProjectCard.TModel;
    created: boolean;
}

const useCreateWikiLinkedCard = (options?: TMutationOptions<ICreateWikiLinkedCardForm, ICreateWikiLinkedCardResponse>) => {
    const { mutate } = useQueryMutation();

    const createLinkedCard = async (form: ICreateWikiLinkedCardForm): Promise<ICreateWikiLinkedCardResponse> => {
        const auth = useAuthStore.getState();
        const actorUID = auth.currentUser?.uid;
        const session = auth.getSessionVersion();
        const token = auth.getToken();
        const url = Utils.String.format(Routing.API.BOARD.WIKI.CREATE_LINKED_CARD, {
            uid: form.project_uid,
            wiki_uid: form.wiki_uid,
        });
        const response = await api.post(url, undefined, {
            ...(form.column_uid ? { params: { column_uid: form.column_uid } } : {}),
            env: { interceptToast: options?.interceptToast } as never,
        });
        const currentAuth = useAuthStore.getState();
        if (currentAuth.currentUser?.uid !== actorUID || currentAuth.getSessionVersion() !== session || currentAuth.getToken() !== token) {
            throw new DOMException("Wiki context changed", "AbortError");
        }
        return {
            card: ProjectCard.Model.fromOne(response.data.card, true),
            created: response.data.created,
        };
    };

    return mutate<ICreateWikiLinkedCardForm, ICreateWikiLinkedCardResponse>(["create-wiki-linked-card"], createLinkedCard, {
        ...options,
        retry: 0,
    });
};

export default useCreateWikiLinkedCard;
