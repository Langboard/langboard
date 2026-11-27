import Checkbox from "@/components/base/Checkbox";
import { User } from "@/core/models";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { useTranslation } from "react-i18next";
import PaginatedTable from "@/components/PaginatedTable";
import UserFirstname from "@/pages/SettingsPage/components/users/UserFirstname";
import UserLastname from "@/pages/SettingsPage/components/users/UserLastname";
import UserActivation from "@/pages/SettingsPage/components/users/UserActivation";
import UserSettingRole from "@/pages/SettingsPage/components/users/UserSettingRole";
import UserApiKeyRole from "@/pages/SettingsPage/components/users/UserApiKeyRole";
import UserMcpRole from "@/pages/SettingsPage/components/users/UserMcpRole";
import UserMoreMenu from "@/pages/SettingsPage/components/users/UserMoreMenu";
import DateDistance from "@/components/DateDistance";
import { useState } from "react";

export interface IUserListProps {
    selectedUsers: string[];
    setSelectedUsers: React.Dispatch<React.SetStateAction<string[]>>;
}

function UserList({ selectedUsers, setSelectedUsers }: IUserListProps) {
    const [t] = useTranslation();
    const { currentUser } = useAppSetting();
    const [fullAccessEmails, setFullAccessEmails] = useState<string[]>([]);

    return (
        <PaginatedTable
            form={{ listType: "User" }}
            disableOutdatedCheck
            modelFilter={(model) => model.isValidUser() && !model.isDeletedUser() && !!model.created_at && model.uid !== currentUser?.uid}
            prepareData={(_, data) => {
                if (data.full_access_emails) {
                    setFullAccessEmails(data.full_access_emails);
                }
            }}
            columns={[
                {
                    key: "uid",
                    header: (users) => (
                        <Checkbox
                            checked={!!users.length && users.length === selectedUsers.length}
                            onClick={() => {
                                setSelectedUsers((prev) => {
                                    if (prev.length === users.length) {
                                        return [];
                                    } else {
                                        return users.map((user) => user.uid);
                                    }
                                });
                            }}
                        />
                    ),
                    align: "center",
                    width: "w-12",
                    render: ({ value: uid }) => (
                        <Checkbox
                            checked={selectedUsers.some((value) => value === uid)}
                            onClick={() => {
                                setSelectedUsers((prev) => {
                                    if (prev.some((value) => value === uid)) {
                                        return prev.filter((value) => value !== uid);
                                    } else {
                                        return [...prev, uid];
                                    }
                                });
                            }}
                        />
                    ),
                },
                {
                    key: "email",
                    header: t("user.Email"),
                    align: "center",
                    width: "w-[calc(calc(100%_/_12_*_3)_-_theme(spacing.12))]",
                    sortable: true,
                    filterable: true,
                    render: ({ row }) => <UserListItemEmail user={row} />,
                },
                {
                    key: "firstname",
                    header: t("user.First Name"),
                    align: "center",
                    width: "w-1/6 truncate",
                    sortable: true,
                    filterable: true,
                    render: ({ row }) => <UserFirstname user={row} />,
                },
                {
                    key: "lastname",
                    header: t("user.Last Name"),
                    align: "center",
                    width: "w-1/6 truncate",
                    sortable: true,
                    filterable: true,
                    render: ({ row }) => <UserLastname user={row} />,
                },
                {
                    key: "activated_at",
                    header: t("settings.Activation"),
                    align: "center",
                    width: "w-1/12",
                    render: ({ row }) => <UserActivation user={row} />,
                },
                {
                    key: "setting_role_actions",
                    header: t("settings.Admin"),
                    align: "center",
                    width: "w-[calc(calc(100%_/_12_*_1.5)_-_theme(spacing.12))]",
                    render: ({ row }) => <UserSettingRole user={row} fullAccessEmails={fullAccessEmails} />,
                },
                {
                    key: "api_key_role_actions",
                    header: t("settings.API key"),
                    align: "center",
                    width: "w-[calc(calc(100%_/_12_*_1)_-_theme(spacing.12))]",
                    render: ({ row }) => <UserApiKeyRole user={row} />,
                },
                {
                    key: "mcp_role_actions",
                    header: t("settings.MCP"),
                    align: "center",
                    width: "w-[calc(calc(100%_/_12_*_1)_-_theme(spacing.12))]",
                    render: ({ row }) => <UserMcpRole user={row} />,
                },
                {
                    key: "created_at",
                    header: t("settings.Created"),
                    align: "center",
                    width: "w-[calc(calc(100%_/_12_*_1.5))]",
                    sortable: true,
                    render: ({ row }) => <UserListItemCreatedAt user={row} />,
                },
                {
                    key: "type",
                    header: t("common.More"),
                    align: "center",
                    width: "w-1/12",
                    render: ({ row }) => <UserMoreMenu user={row} />,
                },
            ]}
        />
    );
}

function UserListItemEmail({ user }: { user: User.TModel }) {
    const email = user.useField("email");
    return <>{email}</>;
}

function UserListItemCreatedAt({ user }: { user: User.TModel }) {
    const createdAt = user.useField("created_at");
    return <DateDistance date={createdAt} />;
}

export default UserList;
