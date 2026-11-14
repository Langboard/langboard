import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import Toast from "@/components/base/Toast";
import Tooltip from "@/components/base/Tooltip";
import useUpdateUserInSettings from "@/controllers/api/settings/users/useUpdateUserInSettings";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import useUpdateDateDistance from "@/core/hooks/useUpdateDateDistance";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { User } from "@/core/models";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { useState } from "react";
import { useTranslation } from "react-i18next";

function UserActivation({ user }: { user: User.TModel }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateUser = hasRoleAction(SettingRole.EAction.UserUpdate);
    const rawActivatedAt = user.useField("activated_at");
    const activatedAt = useUpdateDateDistance(rawActivatedAt);
    const { mutateAsync } = useUpdateUserInSettings(user, { interceptToast: true });
    const [isValidating, setIsValidating] = useState(false);

    const toggle = () => {
        if (isValidating || !canUpdateUser) {
            return;
        }

        setIsValidating(true);

        const activate = !rawActivatedAt;

        const promise = mutateAsync({
            activate,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t(`successes.User ${activate ? "activated" : "deactivated"} successfully.`);
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Tooltip.Root>
            <Tooltip.Trigger asChild>
                <Flex justify="center" w="full" gap="2">
                    <Checkbox checked={!!rawActivatedAt} onClick={toggle} disabled={!canUpdateUser} aria-label={t("settings.Activation")} />
                    <span>{t(rawActivatedAt ? "settings.Active" : "settings.Inactive")}</span>
                </Flex>
            </Tooltip.Trigger>
            <Tooltip.Content side="bottom" align="center">
                {rawActivatedAt ? activatedAt : t("settings.Activate")}
            </Tooltip.Content>
        </Tooltip.Root>
    );
}

export default UserActivation;
