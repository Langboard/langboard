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

export interface ICreateInternalBotForm {
    bot_type: InternalBotModel.EInternalBotType;
    display_name: string;
    platform: EBotPlatform;
    platform_running_type: EBotPlatformRunningType;
    api_url?: string;
    api_key?: string;
    value: string;
    avatar?: File;
}

const useCreateInternalBot = (options?: TMutationOptions<ICreateInternalBotForm>) => {
    const { mutate } = useQueryMutation();

    const createInternalBot = async (params: ICreateInternalBotForm) => {
        const image = params.bot_type === InternalBotModel.EInternalBotType.ImageGeneration;
        const actor = getAuthStore().currentUser;
        const allowed = () =>
            !!actor &&
            getAuthStore().currentUser === actor &&
            actor.setting_role_actions?.some((action) => action === ROLE_ALL_GRANTED || action === SettingRole.EAction.InternalBotCreate);
        if (image && !allowed()) throw new CanceledError("Image profile permission changed");

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

        const res = await api.post(Routing.API.SETTINGS.INTERNAL_BOTS.CREATE, formData, {
            env: {
                interceptToast: image || options?.interceptToast,
                secretInput: image,
            } as never,
        });

        if (image && !allowed()) throw new CanceledError("Image profile changed");

        return res.data;
    };

    const result = mutate(["create-internal-bot"], createInternalBot, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateInternalBot;
