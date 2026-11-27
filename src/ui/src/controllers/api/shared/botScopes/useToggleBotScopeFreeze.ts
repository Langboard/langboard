import { TBotScopeRelatedParams } from "@/controllers/api/shared/botScopes/types";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";
import { TMutationOptions } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";

export type TUseToggleBotScopeFreezeParams = TBotScopeRelatedParams & {
    bot_scope_uid: string;
};

export interface IToggleBotScopeFreezeForm {
    is_frozen: bool;
}

const useToggleBotScopeFreeze = (params: TUseToggleBotScopeFreezeParams, options?: TMutationOptions<IToggleBotScopeFreezeForm>) => {
    const toggleBotScopeFreeze = async (form: IToggleBotScopeFreezeForm) => {
        const url = Utils.String.format(Routing.API.BOT.SCOPE.TOGGLE_FREEZE, {
            bot_uid: params.bot_uid,
            scope_uid: params.bot_scope_uid,
        });
        const res = await api.put(
            url,
            {
                target_table: params.target_table,
                is_frozen: form.is_frozen,
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

    return { mutateAsync: toggleBotScopeFreeze };
};

export default useToggleBotScopeFreeze;
