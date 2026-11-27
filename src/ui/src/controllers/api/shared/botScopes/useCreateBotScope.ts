import { TBotScopeRelatedParams } from "@/controllers/api/shared/botScopes/types";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { EBotTriggerCondition } from "@/core/models/botScopes/EBotTriggerCondition";
import { Utils } from "@langboard/core/utils";

export interface ICreateBotScopeForm {
    conditions: EBotTriggerCondition[];
}

const useCreateBotScope = (params: TBotScopeRelatedParams, options?: TMutationOptions<ICreateBotScopeForm>) => {
    const { mutate } = useQueryMutation();

    const createBotScope = async (form: ICreateBotScopeForm) => {
        const url = Utils.String.format(Routing.API.BOT.SCOPE.CREATE, {
            bot_uid: params.bot_uid,
        });
        const res = await api.post(
            url,
            {
                target_table: params.target_table,
                target_uid: params.target_uid,
                conditions: form.conditions,
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        const targetModel = BOT_SCOPES[res.data.scope_table as keyof typeof BOT_SCOPES];
        if (targetModel) {
            targetModel.Model.fromOne(res.data.bot_scope, true);
        }

        return res.data;
    };

    const result = mutate(["create-bot-scope"], createBotScope, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateBotScope;
