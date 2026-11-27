import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";
import ModelEdgeStore from "@/core/models/ModelEdgeStore";
import { ProjectCheckitem, ProjectChecklist } from "@/core/models";

export interface ICreateCardCheckitemForm {
    project_uid: string;
    card_uid: string;
    checklist_uid: string;
    title: string;
}

interface ICreateCardCheckitemRawResponse {
    checkitem: ProjectCheckitem.Interface;
}

const useCreateCardCheckitem = (options?: TMutationOptions<ICreateCardCheckitemForm>) => {
    const { mutate } = useQueryMutation();

    const createCheckitem = async (params: ICreateCardCheckitemForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.CHECKITEM.CREATE, {
            uid: params.project_uid,
            card_uid: params.card_uid,
            checklist_uid: params.checklist_uid,
        });
        const res = await api.post<ICreateCardCheckitemRawResponse>(
            url,
            {
                title: params.title,
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        const checkitem = ProjectCheckitem.Model.fromOne(res.data.checkitem, true);
        const checklist = ProjectChecklist.Model.getModel(params.checklist_uid);
        if (checklist) {
            ModelEdgeStore.addEdge(checklist, checkitem);
        }

        return res.data;
    };

    const result = mutate(["create-card-checkitem"], createCheckitem, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateCardCheckitem;
