import Tabs from "@/components/base/Tabs";
import GroupDirectory from "./components/users/GroupDirectory";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useDeleteSelectedUsersInSettings from "@/controllers/api/settings/users/useDeleteSelectedUsersInSettings";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import EmployeeMembershipSettings from "@/pages/SettingsPage/components/users/EmployeeMembershipSettings";
import UserList from "@/pages/SettingsPage/components/users/UserList";
import { EHttpStatus } from "@langboard/core/enums";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

function UsersPage() {
    const { setPageAliasRef } = usePageHeader();
    useEffect(() => {
        setPageAliasRef.current("Users");
    }, []);
    const { currentUser } = useAppSetting();
    const [t] = useTranslation();
    const actions = currentUser.useField("setting_role_actions");
    const isAdmin = currentUser.useField("is_admin");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const canUsers = isAdmin && hasRoleAction(...SettingRole.CATEGORIZED_MAP.User);
    const canGroups = isAdmin && hasRoleAction(SettingRole.EAction.GroupRead);
    const [tab, setTab] = useState("users");
    const active = tab === "users" && canUsers ? "users" : tab === "groups" && canGroups ? "groups" : canUsers ? "users" : "groups";
    if (!canUsers && !canGroups) return <p role="alert">{t("settings.Directory forbidden")}</p>;
    return (
        <Tabs.Provider value={active} onValueChange={setTab}>
            <Tabs.List
                role="tablist"
                aria-label={t("settings.Users")}
                className="mb-4 overflow-hidden"
                onKeyDown={(event) => {
                    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=tab]"));
                    const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
                    const next =
                        event.key === "Home"
                            ? 0
                            : event.key === "End"
                              ? tabs.length - 1
                              : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
                    event.preventDefault();
                    tabs[next]?.focus();
                    tabs[next]?.click();
                }}
            >
                {canUsers && (
                    <Tabs.Trigger
                        role="tab"
                        tabIndex={active === "users" ? 0 : -1}
                        aria-selected={active === "users"}
                        aria-controls="directory-users"
                        id="tab-users"
                        value="users"
                    >
                        {t("settings.Users")}
                    </Tabs.Trigger>
                )}
                {canGroups && (
                    <Tabs.Trigger
                        role="tab"
                        tabIndex={active === "groups" ? 0 : -1}
                        aria-selected={active === "groups"}
                        aria-controls="directory-groups"
                        id="tab-groups"
                        value="groups"
                    >
                        {t("settings.Groups")}
                    </Tabs.Trigger>
                )}
            </Tabs.List>
            {active === "users" && canUsers && (
                <div role="tabpanel" id="directory-users" aria-labelledby="tab-users">
                    <UserSettings key={currentUser.uid} />
                </div>
            )}
            {active === "groups" && canGroups && (
                <div role="tabpanel" id="directory-groups" aria-labelledby="tab-groups">
                    <GroupDirectory key={currentUser.uid} />
                </div>
            )}
        </Tabs.Provider>
    );
}

function UserSettings() {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser, isValidating, setIsValidating } = useAppSetting();
    const [selectedUsers, setSelectedUsers] = useState<string[]>([]);
    const { mutate: deleteSelectedUsersMutate } = useDeleteSelectedUsersInSettings();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canReadUser = hasRoleAction(SettingRole.EAction.UserRead);
    const canDeleteUser = hasRoleAction(SettingRole.EAction.UserDelete);

    const openCreateDialog = () => {
        navigate(ROUTES.SETTINGS.CREATE_USER);
    };

    const deleteSelectedUsers = () => {
        if (isValidating || !selectedUsers.length || !canDeleteUser) {
            return;
        }

        setIsValidating(true);

        deleteSelectedUsersMutate(
            {
                user_uids: selectedUsers,
            },
            {
                onSuccess: () => {
                    Toast.Add.success(t("successes.Selected users deleted successfully."));
                    setSelectedUsers([]);
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    return (
        <>
            <Flex
                justify={{ sm: "between" }}
                direction={{ initial: "col", sm: "row" }}
                gap="2"
                mb="4"
                pb="2"
                textSize="3xl"
                weight="semibold"
                className="scroll-m-20 tracking-tight"
            >
                <span className="w-36">{t("settings.Users")}</span>
                <Flex gap="2" wrap justify="end" maxW={{ initial: "full", sm: "auto" }}>
                    {selectedUsers.length > 0 && canDeleteUser && (
                        <Button variant="destructive" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={deleteSelectedUsers}>
                            <IconComponent icon="trash" size="4" />
                            {t("common.Delete")}
                        </Button>
                    )}
                    {hasRoleAction(SettingRole.EAction.UserCreate) && (
                        <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={openCreateDialog}>
                            <IconComponent icon="plus" size="4" />
                            {t("settings.Add new")}
                        </Button>
                    )}
                </Flex>
            </Flex>
            {canReadUser && <EmployeeMembershipSettings />}
            {canReadUser && <UserList selectedUsers={selectedUsers} setSelectedUsers={setSelectedUsers} />}
        </>
    );
}

export default UsersPage;
