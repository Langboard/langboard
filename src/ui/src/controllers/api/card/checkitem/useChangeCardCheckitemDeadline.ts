import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";

export interface IChangeCardCheckitemDeadlineForm {
    project_uid: string;
    card_uid: string;
    checkitem_uid: string;
    deadline_at: Date | "";
}

const useChangeCardCheckitemDeadline = (options?: TMutationOptions<IChangeCardCheckitemDeadlineForm>) => {
    const { mutate } = useQueryMutation();

    const changeCheckitemDeadline = async (params: IChangeCardCheckitemDeadlineForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.CHECKITEM.CHANGE_DEADLINE, {
            uid: params.project_uid,
            card_uid: params.card_uid,
            checkitem_uid: params.checkitem_uid,
        });
        const res = await api.put(
            url,
            {
                deadline_at: params.deadline_at,
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        return res.data;
    };

    const result = mutate(["change-card-checkitem-deadline"], changeCheckitemDeadline, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useChangeCardCheckitemDeadline;
