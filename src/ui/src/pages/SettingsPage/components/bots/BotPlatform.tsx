import Box from "@/components/base/Box";
import Toast from "@/components/base/Toast";
import BotPlatformSelect from "@/components/bots/BotPlatformSelect";
import useUpdateBot from "@/controllers/api/settings/bots/useUpdateBot";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EBotPlatform } from "@langboard/core/ai";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const BotPlatform = memo(() => {
    const [t] = useTranslation();
    const { model: internalBot } = ModelRegistry.BotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateBot = hasRoleAction(SettingRole.EAction.BotUpdate);
    const platform = internalBot.useField("platform");
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync } = useUpdateBot(internalBot, { interceptToast: true });

    const changePlatform = async (value: EBotPlatform) => {
        if (isValidating || !canUpdateBot) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            platform: value,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.Bot platform changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Box>
            <BotPlatformSelect state={[platform, changePlatform]} isValidating={isValidating} disabled={!canUpdateBot} />
        </Box>
    );
});

export default BotPlatform;
