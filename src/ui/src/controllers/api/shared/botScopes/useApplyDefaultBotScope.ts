import { TBotScopeRelatedParams } from "@/controllers/api/shared/botScopes/types";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";

export interface IApplyDefaultBotScopeForm {
    target_table: TBotScopeRelatedParams["target_table"];
    target_uid: string;
    default_scope_branch_uid?: string | null;
}

const useApplyDefaultBotScope = (params: Pick<TBotScopeRelatedParams, "bot_uid">, options?: TMutationOptions<IApplyDefaultBotScopeForm>) => {
    const { mutate } = useQueryMutation();

    const applyDefaultBotScope = async (form: IApplyDefaultBotScopeForm) => {
        const url = Utils.String.format(Routing.API.BOT.SCOPE.APPLY_DEFAULT, {
            bot_uid: params.bot_uid,
        });
        const res = await api.put(url, form, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        const targetModel = BOT_SCOPES[res.data.scope_table as keyof typeof BOT_SCOPES];
        if (targetModel) {
            targetModel.Model.fromOne(res.data.bot_scope, true);
        }

        return res.data;
    };

    const result = mutate(["apply-default-bot-scope"], applyDefaultBotScope, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useApplyDefaultBotScope;
