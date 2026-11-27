import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import Toast from "@/components/base/Toast";
import Box from "@/components/base/Box";
import Popover from "@/components/base/Popover";
import Tooltip from "@/components/base/Tooltip";
import IconComponent from "@/components/base/IconComponent";
import useUpdateUserApiKeyRole from "@/controllers/api/settings/users/useUpdateUserApiKeyRole";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { User } from "@/core/models";
import { ApiKeyRole, SettingRole } from "@/core/models/roles";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";
import { useState } from "react";
import { useTranslation } from "react-i18next";

function UserApiKeyRole({ user }: { user: User.TModel }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateUser = hasRoleAction(SettingRole.EAction.UserUpdate);
    const { mutateAsync: updateRole } = useUpdateUserApiKeyRole(user, { interceptToast: true });
    const [isValidating, setIsValidating] = useState(false);
    const apiKeyRoles = user.useField("api_key_role_actions");
    const isAdmin = user.useField("is_admin");

    const READ_ACTION = ApiKeyRole.EAction.Read;
    const DEPENDENT_ACTIONS = [ApiKeyRole.EAction.Create, ApiKeyRole.EAction.Update, ApiKeyRole.EAction.Delete];

    const isAllGranted = () => {
        if (isAdmin) return true;
        return apiKeyRoles && apiKeyRoles.includes(ROLE_ALL_GRANTED);
    };

    const isActionSelected = (action: ApiKeyRole.TActions) => {
        if (isAdmin) return true;
        return apiKeyRoles && (apiKeyRoles.includes(action) || apiKeyRoles.includes(ROLE_ALL_GRANTED));
    };

    const toggleAction = (action: ApiKeyRole.TActions) => {
        if (isValidating || isAdmin || !canUpdateUser) return;
        setIsValidating(true);

        const hasAllGranted = apiKeyRoles && apiKeyRoles.includes(ROLE_ALL_GRANTED);
        const isSelected = apiKeyRoles && (apiKeyRoles.includes(action) || apiKeyRoles.includes(ROLE_ALL_GRANTED));
        let newActions: ApiKeyRole.TActions[];

        action = Utils.String.convertSafeEnum(ApiKeyRole.EAction, action);
        if (isSelected) {
            if (hasAllGranted) {
                newActions = Object.values(ApiKeyRole.EAction).filter((currentAction) => currentAction !== action);
                if (action === READ_ACTION) {
                    newActions = [];
                }
            } else {
                newActions = apiKeyRoles.filter((currentAction) => currentAction !== action);
                if (action === READ_ACTION) {
                    newActions = [];
                }
            }
        } else if (apiKeyRoles) {
            if (DEPENDENT_ACTIONS.includes(action)) {
                newActions = [...new Set([...apiKeyRoles, action, READ_ACTION])];
            } else {
                newActions = [...apiKeyRoles, action];
            }
        } else {
            if (DEPENDENT_ACTIONS.includes(action)) {
                newActions = [action, READ_ACTION];
            } else {
                newActions = [action];
            }
        }

        const promise = updateRole({ actions: newActions });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler(
                    {
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => {
                                navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true });
                            },
                        },
                    },
                    messageRef
                );

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Role updated successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const toggleAll = () => {
        if (isValidating || isAdmin || !canUpdateUser) return;
        setIsValidating(true);

        const promise = updateRole({ actions: isAllGranted() ? [] : Object.values(ApiKeyRole.EAction) });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);
                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Role updated successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const getTooltipText = () => {
        if (isAdmin) return t("settings.Full access");
        if (apiKeyRoles && apiKeyRoles.includes(ROLE_ALL_GRANTED)) {
            return t("settings.Full access");
        }
        if (apiKeyRoles && apiKeyRoles.length > 0) {
            return t("settings.Partial access ({{count}} permissions)", { count: apiKeyRoles.length });
        }
        return t("settings.No Permissions");
    };

    const allActions = Object.values(ApiKeyRole.EAction);

    if (isAdmin) {
        return (
            <Flex justify="center" items="center" w="full">
                <Tooltip.Root>
                    <Tooltip.Trigger asChild>
                        <IconComponent icon="key" size="4" className="text-gray-400" />
                    </Tooltip.Trigger>
                    <Tooltip.Content side="bottom" align="center">
                        {t("settings.Full access")}
                    </Tooltip.Content>
                </Tooltip.Root>
            </Flex>
        );
    }

    return (
        <Popover.Root>
            <Popover.Trigger asChild>
                <Button variant="ghost" size="icon-sm" title={getTooltipText()} titleAlign="center" titleSide="bottom" disabled={!canUpdateUser}>
                    <IconComponent icon="key" size="4" />
                </Button>
            </Popover.Trigger>
            <Popover.Content className="w-96" align="start" side="left">
                <Box p="2">
                    <Flex justify="between" items="center" mb="3">
                        <Box weight="bold">{t("settings.API key permissions")}</Box>
                        <Checkbox
                            checked={!!isAllGranted()}
                            onClick={toggleAll}
                            disabled={isValidating || isAdmin || !canUpdateUser}
                            label={<Box textSize="xs">{t("settings.Toggle all")}</Box>}
                        />
                    </Flex>

                    <Box>
                        <Flex wrap gap="3">
                            {allActions.map((action) => (
                                <Checkbox
                                    key={action}
                                    checked={isActionSelected(action)}
                                    onClick={() => toggleAction(action)}
                                    disabled={isValidating || isAdmin || !canUpdateUser}
                                    label={t(`role.api_key.${action}`)}
                                />
                            ))}
                        </Flex>
                    </Box>
                </Box>
            </Popover.Content>
        </Popover.Root>
    );
}

export default UserApiKeyRole;
