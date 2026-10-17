import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Toast from "@/components/base/Toast";
import useChangeInternalBotDefault from "@/controllers/api/settings/internalBots/useChangeDefaultInternalBot";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const InternalBotDefault = memo(() => {
    const [t] = useTranslation();
    const { model: internalBot } = ModelRegistry.InternalBotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateInternalBot = hasRoleAction(SettingRole.EAction.InternalBotUpdate);
    const isDefault = internalBot.useField("is_default");
    const { mutateAsync } = useChangeInternalBotDefault(internalBot, { interceptToast: true });
    const [isValidating, setIsValidating] = useState(false);

    const change = () => {
        if (isValidating || !canUpdateInternalBot) {
            return;
        }

        const promise = mutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.Internal bot default changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Box>
            <Button size="sm" onClick={change} disabled={!canUpdateInternalBot || isDefault || isValidating}>
                {t("settings.Set as default")}
            </Button>
        </Box>
    );
});

export default InternalBotDefault;
