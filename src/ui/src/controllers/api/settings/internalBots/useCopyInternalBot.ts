import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { InternalBotModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

const useCopyInternalBot = (bot: InternalBotModel.TModel, options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();

    const copyInternalBot = async () => {
        const url = Utils.String.format(Routing.API.SETTINGS.INTERNAL_BOTS.COPY, { bot_uid: bot.uid });
        const res = await api.post(url, undefined, {
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        InternalBotModel.Model.fromOne(res.data.internal_bot, true);

        return res.data;
    };

    const result = mutate(["copy-internal-bot"], copyInternalBot, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCopyInternalBot;
