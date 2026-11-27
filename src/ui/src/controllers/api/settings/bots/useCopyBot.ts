import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { BotModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

export interface ICopyBotResponse {
    revealed_app_api_token: string;
}

const useCopyBot = (bot: BotModel.TModel, options?: TMutationOptions<unknown, ICopyBotResponse>) => {
    const { mutate } = useQueryMutation();

    const copyBot = async () => {
        const url = Utils.String.format(Routing.API.SETTINGS.BOTS.COPY, { bot_uid: bot.uid });
        const res = await api.post(url, undefined, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        BotModel.Model.fromOne(res.data.bot, true);

        return {
            revealed_app_api_token: res.data.revealed_app_api_token,
        };
    };

    const result = mutate(["copy-bot"], copyBot, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCopyBot;
