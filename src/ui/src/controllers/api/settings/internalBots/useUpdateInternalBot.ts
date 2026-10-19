import { Routing } from "@langboard/core/constants";
import { CanceledError } from "axios";
import { getAuthStore } from "@/core/stores/AuthStore";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";
import { SettingRole } from "@/core/models/roles";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { InternalBotModel } from "@/core/models";
import { EBotPlatform, EBotPlatformRunningType } from "@langboard/core/ai";
import { Utils } from "@langboard/core/utils";

export interface IUpdateInternalBotForm {
    display_name?: string;
    platform?: EBotPlatform;
    platform_running_type?: EBotPlatformRunningType;
    api_url?: string;
    api_key?: string;
    value?: string;
    avatar?: File;
    delete_avatar?: bool;
}

const useUpdateInternalBot = (bot: InternalBotModel.TModel, options?: TMutationOptions<IUpdateInternalBotForm>) => {
    const { mutate } = useQueryMutation();

    const updateInternalBot = async (params: IUpdateInternalBotForm) => {
        const image = bot.bot_type === InternalBotModel.EInternalBotType.ImageGeneration;
        const actor = getAuthStore().currentUser;
        const allowed = () =>
            !!actor &&
            getAuthStore().currentUser === actor &&
            actor.setting_role_actions.some((action) => action === ROLE_ALL_GRANTED || action === SettingRole.EAction.InternalBotUpdate);
        if (image && !allowed()) throw new CanceledError("Image profile permission changed");
        const originalValue = bot.value;
        const originalUpdatedAt = bot.updated_at;

        const url = Utils.String.format(Routing.API.SETTINGS.INTERNAL_BOTS.UPDATE, { bot_uid: bot.uid });
        const formData = new FormData();
        Object.entries(params).forEach(([key, value]) => {
            if (Utils.Type.isNullOrUndefined(value)) {
                return;
            }

            const isAvatar = (targetKey: string, _: unknown): _ is File => targetKey === "avatar";

            if (isAvatar(key, value)) {
                if (!value) {
                    return;
                }

                formData.append(key, value, value.name);
            } else {
                formData.append(key, value.toString());
            }
        });

        const res = await api.put(url, formData, {
            env: {
                interceptToast: image || options?.interceptToast,
                secretInput: image,
            } as never,
        });

        if (image && (!allowed() || bot.value !== originalValue || bot.updated_at !== originalUpdatedAt))
            throw new CanceledError("Image profile changed");

        if (Utils.Type.isObject(res.data?.internal_bot)) {
            InternalBotModel.Model.fromOne(res.data.internal_bot as InternalBotModel.Interface, true);
        }

        return res.data;
    };

    const result = mutate(["update-internal-bot"], updateInternalBot, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useUpdateInternalBot;
