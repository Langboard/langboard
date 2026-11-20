import Box from "@/components/base/Box";
import Toast from "@/components/base/Toast";
import MoreMenu from "@/components/MoreMenu";
import { DISABLE_DRAGGING_ATTR } from "@/constants";
import useDeleteUserInSettings from "@/controllers/api/settings/users/useDeleteUserInSettings";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { User } from "@/core/models";
import UserDetails from "@/pages/SettingsPage/components/users/UserDetails";
import { SettingRole } from "@/core/models/roles";
import { memo } from "react";
import { useTranslation } from "react-i18next";

const UserMoreMenu = memo(({ user }: { user: User.TModel }) => {
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canDeleteUser = hasRoleAction(SettingRole.EAction.UserDelete);
    const canReadUser = hasRoleAction(SettingRole.EAction.UserRead);
    const canReadAudit = hasRoleAction(SettingRole.EAction.UserAuditRead);

    if (!canDeleteUser && !canReadUser) {
        return null;
    }

    return (
        <MoreMenu.Root
            triggerProps={{ className: "size-7", titleSide: "bottom", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
            contentProps={{ className: "w-min p-0", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
        >
            {canReadUser && <UserDetails user={user} canReadAudit={canReadAudit} />}
            {canDeleteUser && <UserMoreMenuDelete user={user} />}
        </MoreMenu.Root>
    );
});

const UserMoreMenuDelete = memo(({ user }: { user: User.TModel }) => {
    const [t] = useTranslation();
    const { mutateAsync } = useDeleteUserInSettings(user, { interceptToast: true });

    const deleteUser = (endCallback: (shouldClose: bool) => void) => {
        const promise = mutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Deleting..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.User deleted successfully.");
            },
            finally: () => {
                endCallback(true);
            },
        });
    };

    return (
        <MoreMenu.PopoverItem
            modal
            contentProps={{ align: "center", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
            menuName={t("settings.Delete user")}
            saveText={t("common.Delete")}
            saveButtonProps={{ variant: "destructive" }}
            onSave={deleteUser}
        >
            <Box mb="1" textSize={{ initial: "sm", sm: "base" }} weight="semibold" className="text-center">
                {t("ask.Are you sure you want to delete this user?")}
            </Box>
            <Box maxW="full" textSize="sm" weight="bold" className="text-center text-red-500">
                {t("common.deleteDescriptions.This action cannot be undone.")}
            </Box>
        </MoreMenu.PopoverItem>
    );
});

export default UserMoreMenu;
