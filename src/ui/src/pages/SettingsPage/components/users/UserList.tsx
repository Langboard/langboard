import Checkbox from "@/components/base/Checkbox";
import { User } from "@/core/models";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { useTranslation } from "react-i18next";
import DataTable from "@/components/base/DataTable";
import { IPaginateOptions } from "@/components/base/DataTable/Provider";
import Button from "@/components/base/Button";
import Select from "@/components/base/Select";
import { api } from "@/core/helpers/Api";
import UserFirstname from "@/pages/SettingsPage/components/users/UserFirstname";
import UserLastname from "@/pages/SettingsPage/components/users/UserLastname";
import UserActivation from "@/pages/SettingsPage/components/users/UserActivation";
import UserSettingRole from "@/pages/SettingsPage/components/users/UserSettingRole";
import UserApiKeyRole from "@/pages/SettingsPage/components/users/UserApiKeyRole";
import UserMcpRole from "@/pages/SettingsPage/components/users/UserMcpRole";
import UserMoreMenu from "@/pages/SettingsPage/components/users/UserMoreMenu";
import DateDistance from "@/components/DateDistance";
import { useEffect, useState } from "react";

export interface IUserListProps {
    selectedUsers: string[];
    setSelectedUsers: React.Dispatch<React.SetStateAction<string[]>>;
}

function UserList({ selectedUsers, setSelectedUsers }: IUserListProps) {
    const [t] = useTranslation();
    const { currentUser } = useAppSetting();
    const [fullAccessEmails, setFullAccessEmails] = useState<string[]>([]);

    const [page, setPage] = useState(1);
    const [search, setSearch] = useState("");
    const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
    const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({ key: "", direction: "asc" });
    const [accountStatus, setAccountStatus] = useState("all");
    const [rows, setRows] = useState<User.TModel[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const [revision, setRevision] = useState(0);
    const pageLimit = 15;

    useEffect(() => {
        const abort = new AbortController();
        setLoading(true);
        setError(false);
        setSelectedUsers([]);
        api.get("/settings/users/page", {
            signal: abort.signal,
            params: {
                page,
                limit: pageLimit,
                query: search,
                account_status: accountStatus,
                ...columnFilters,
                exclude_user_uid: currentUser.uid,
                sort_by: sort.key || "created_at",
                sort_direction: sort.key ? sort.direction : "desc",
            },
        })
            .then(({ data }) => {
                if (abort.signal.aborted) return;
                const lastPage = Math.max(1, Math.ceil(data.total_records / pageLimit));
                if (page > lastPage) {
                    setPage(lastPage);
                    return;
                }
                setRows(User.Model.fromArray(data.records, true));
                setTotal(data.total_records);
                setFullAccessEmails(data.full_access_emails ?? []);
            })
            .catch(() => {
                if (!abort.signal.aborted) {
                    setRows([]);
                    setTotal(0);
                    setError(true);
                }
            })
            .finally(() => {
                if (!abort.signal.aborted) setLoading(false);
            });
        return () => abort.abort();
    }, [page, search, columnFilters, sort, accountStatus, currentUser.uid, revision, setSelectedUsers]);

    const paginate = (options: IPaginateOptions) => {
        setPage(options.page ?? 1);
        if (options.search !== undefined) setSearch(options.search);
        if (options.columnFilters) setColumnFilters(options.columnFilters);
        if (options.sort) setSort(options.sort);
    };

    return (
        <div aria-busy={loading}>
            <div className="mb-3 flex items-center gap-3">
                <Select.Root
                    value={accountStatus}
                    onValueChange={(value) => {
                        setAccountStatus(value);
                        setPage(1);
                    }}
                >
                    <Select.Trigger className="w-48" aria-label={t("settings.Account status")}>
                        <Select.Value />
                    </Select.Trigger>
                    <Select.Content>
                        <Select.Item value="all">{t("settings.All accounts")}</Select.Item>
                        <Select.Item value="active">{t("settings.Active")}</Select.Item>
                        <Select.Item value="inactive">{t("settings.Inactive")}</Select.Item>
                    </Select.Content>
                </Select.Root>
                <Button variant="outline" disabled={loading} onClick={() => setRevision((value) => value + 1)}>
                    {t("common.Refresh")}
                </Button>
            </div>
            {error && (
                <p role="alert" className="mb-3 text-destructive">
                    {t("settings.User list failed")}
                </p>
            )}
            <DataTable<User.TModel>
                data={rows.filter((row) => row.isValidUser() && !row.isDeletedUser())}
                totalRecords={total}
                itemsPerPage={pageLimit}
                loading={loading}
                searchable
                showPagination
                remote={{ page, search, columnFilters, sort, onChange: paginate }}
                columns={[
                    {
                        key: "uid",
                        header: (users) => (
                            <Checkbox
                                disabled={loading}
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
                                disabled={loading}
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
                        header: t("settings.Account status"),
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
                        key: "last_signed_in_at",
                        header: t("settings.Last successful sign-in"),
                        align: "center",
                        width: "min-w-40",
                        render: ({ row }) => <UserListItemLastSignIn user={row} />,
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
        </div>
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

function UserListItemLastSignIn({ user }: { user: User.TModel }) {
    const [t] = useTranslation();
    const signedInAt = user.useField("last_signed_in_at");
    return signedInAt ? <DateDistance date={signedInAt} /> : <span className="text-muted-foreground">{t("settings.No recorded sign-in")}</span>;
}

export default UserList;
