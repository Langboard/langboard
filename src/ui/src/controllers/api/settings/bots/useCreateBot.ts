import { CanceledError } from "axios";
import useAuthStore, { getAuthStore } from "@/core/stores/AuthStore";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { BotModel } from "@/core/models";
import { EBotPlatform, EBotPlatformRunningType } from "@langboard/core/ai";

export interface ICreateBotForm {
    bot_name: string;
    bot_uname: string;
    platform: EBotPlatform;
    platform_running_type: EBotPlatformRunningType;
    api_url?: string;
    api_key?: string;
    ip_whitelist: string[];
    value?: string;
    avatar?: File;
}

export interface ICreateBotResponse {
    revealed_app_api_token: string;
}

const useCreateBot = (options?: TMutationOptions<ICreateBotForm, ICreateBotResponse>) => {
    const { mutate } = useQueryMutation();

    const createBot = async (form: ICreateBotForm) => {
        const formData = new FormData();
        Object.entries(form).forEach(([key, value]) => {
            if (!value) {
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

        const actor = getAuthStore().currentUser;
        const identity = getAuthStore().getIdentityVersion();
        const authority = JSON.stringify([actor?.is_admin, actor?.setting_role_actions]);
        let invalidated = false;
        const unsubscribe = actor?.subscribeFields(["is_admin", "setting_role_actions"], () => {
            invalidated = true;
        });
        const unsubscribeActor = useAuthStore.subscribe((state) => {
            if (state.currentUser !== actor || state.getIdentityVersion() !== identity) invalidated = true;
        });
        const isCurrent = () =>
            !!actor &&
            !invalidated &&
            getAuthStore().currentUser === actor &&
            getAuthStore().getIdentityVersion() === identity &&
            JSON.stringify([actor.is_admin, actor.setting_role_actions]) === authority;
        try {
            const res = await api.post(Routing.API.SETTINGS.BOTS.CREATE, formData, {
                // The caller handles errors after checking that this actor still owns the draft.
                env: { interceptToast: true } as never,
            });
            if (!isCurrent()) throw new CanceledError("Bot creation authority changed");
            BotModel.Model.fromOne(res.data.bot, true);

            return {
                revealed_app_api_token: res.data.revealed_app_api_token,
            };
        } catch (error) {
            if (!isCurrent()) throw new CanceledError("Bot creation authority changed");
            throw error;
        } finally {
            unsubscribe?.();
            unsubscribeActor();
        }
    };

    const result = mutate(["create-bot"], createBot, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateBot;
