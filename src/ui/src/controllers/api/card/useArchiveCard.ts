import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";
import { ProjectCard, ProjectWiki } from "@/core/models";
import { deleteCardModel } from "@/core/helpers/ModelHelper";

export interface IArchiveCardForm {
    project_uid: string;
    card_uid: string;
}

const useArchiveCard = (options?: TMutationOptions<IArchiveCardForm>) => {
    const { mutate } = useQueryMutation();

    const archiveCard = async (params: IArchiveCardForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.ARCHIVE, {
            uid: params.project_uid,
            card_uid: params.card_uid,
        });
        const res = await api.put(url, undefined, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        const card = ProjectCard.Model.getModel(params.card_uid);
        if (card?.project_uid === params.project_uid && card.source_type === "project_wiki") {
            const wiki = card.source_uid ? ProjectWiki.Model.getModel(card.source_uid) : undefined;
            if (wiki?.project_uid === params.project_uid && wiki.linked_card_uid === card.uid) wiki.linked_card_uid = undefined;
            deleteCardModel(card.uid, true);
        }

        return res.data;
    };

    const result = mutate(["archive-card"], archiveCard, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useArchiveCard;
