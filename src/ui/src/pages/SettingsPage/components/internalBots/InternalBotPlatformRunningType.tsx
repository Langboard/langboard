import Box from "@/components/base/Box";
import Toast from "@/components/base/Toast";
import BotPlatformRunningTypeSelect from "@/components/bots/BotPlatformRunningTypeSelect";
import useUpdateInternalBot from "@/controllers/api/settings/internalBots/useUpdateInternalBot";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EBotPlatformRunningType } from "@langboard/core/ai";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const InternalBotPlatformRunningType = memo(() => {
    const [t] = useTranslation();
    const { model: internalBot } = ModelRegistry.InternalBotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateInternalBot = hasRoleAction(SettingRole.EAction.InternalBotUpdate);
    const platform = internalBot.useField("platform");
    const platformRunningType = internalBot.useField("platform_running_type");
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync } = useUpdateInternalBot(internalBot, { interceptToast: true });

    const changePlatformRunningType = async (value: EBotPlatformRunningType) => {
        if (isValidating || !canUpdateInternalBot) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            platform_running_type: value,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.Internal bot platform running type changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Box>
            <BotPlatformRunningTypeSelect
                state={[platformRunningType, changePlatformRunningType]}
                platform={platform}
                isValidating={isValidating}
                disabled={!canUpdateInternalBot}
            />
        </Box>
    );
});

export default InternalBotPlatformRunningType;
