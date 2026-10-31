import UserAvatar from "@/components/UserAvatar";
import UserListIdentitySources from "./UserIdentitySources";
import Input from "@/components/base/Input";
import { localSignInBound } from "./userLoginRange";
import Checkbox from "@/components/base/Checkbox";
import { User } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
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
import { useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export interface IUserListProps {
    selectedUsers: string[];
    setSelectedUsers: React.Dispatch<React.SetStateAction<string[]>>;
}

function UserList({ selectedUsers, setSelectedUsers }: IUserListProps) {
    const [t] = useTranslation();
    const { currentUser } = useAppSetting();
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const [fullAccessEmails, setFullAccessEmails] = useState<string[]>([]);

    const [page, setPage] = useState(1);
    const [search, setSearch] = useState("");
    const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
    const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({ key: "", direction: "asc" });
    const [accountStatus, setAccountStatus] = useState("all");
    const [identityProvider, setIdentityProvider] = useState("all");
    const [signInFrom, setSignInFrom] = useState("");
    const [signInBefore, setSignInBefore] = useState("");
    const [signInRange, setSignInRange] = useState<{ from?: string; before?: string }>({});
    const [rangeError, setRangeError] = useState(false);
    const [businessActivityFrom, setBusinessActivityFrom] = useState("");
    const [businessActivityBefore, setBusinessActivityBefore] = useState("");
    const [businessActivityRange, setBusinessActivityRange] = useState<{ from?: string; before?: string }>({});
    const [businessActivityRangeError, setBusinessActivityRangeError] = useState(false);
    const [rows, setRows] = useState<User.TModel[]>([]);
    const [total, setTotal] = useState(0);
    const summaryIdentity = JSON.stringify([
        currentUser.uid,
        sessionVersion,
        search,
        columnFilters,
        identityProvider,
        signInRange,
        businessActivityRange,
    ]);
    const [summary, setSummary] = useState<{ identity: string; counts: { total: number; active: number; inactive: number } }>();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const [revision, setRevision] = useState(0);
    const pageLimit = 15;
    const queryClient = useQueryClient();
    const subscriptionKey = useId();
    const applyingPage = useRef(false);
    const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    useEffect(() => () => clearTimeout(refreshTimer.current), []);

    useEffect(() => {
        const refresh = () => {
            if (applyingPage.current) return;
            clearTimeout(refreshTimer.current);
            refreshTimer.current = setTimeout(() => setRevision((value) => value + 1), 0);
        };
        const unsubscribeCreation = User.Model.subscribe("CREATION", subscriptionKey, refresh, () => true);
        const unsubscribeDeletion = User.Model.subscribe("DELETION", subscriptionKey, refresh);
        const unsubscribeRows = rows.map((row) =>
            row.subscribeFields(["type", "firstname", "lastname", "email", "username", "activated_at"], refresh)
        );
        const unsubscribeMutations = queryClient.getMutationCache().subscribe((event) => {
            if (event.type !== "updated" || event.action.type !== "success") return;
            const key = event.mutation.options.mutationKey?.[0];
            if (
                typeof key === "string" &&
                (key === "create-user-in-settings" ||
                    key === "update-user-in-settings" ||
                    key === "delete-selected-users-in-settings" ||
                    key.startsWith("delete-user-in-settings-"))
            ) {
                refresh();
            }
        });
        return () => {
            unsubscribeCreation();
            unsubscribeDeletion();
            unsubscribeRows.forEach((unsubscribe) => unsubscribe());
            unsubscribeMutations();
        };
    }, [rows, queryClient, subscriptionKey]);

    useEffect(() => {
        const abort = new AbortController();
        const current = () =>
            !abort.signal.aborted &&
            sessionVersion === useAuthStore.getState().getSessionVersion() &&
            currentUser.uid === useAuthStore.getState().currentUser?.uid;
        setLoading(true);
        setError(false);
        setSummary(undefined);
        setSelectedUsers([]);
        api.get("/settings/users/page", {
            signal: abort.signal,
            params: {
                page,
                limit: pageLimit,
                query: search,
                account_status: accountStatus,
                identity_provider: identityProvider,
                last_signed_in_from: signInRange.from,
                last_signed_in_before: signInRange.before,
                last_business_activity_from: businessActivityRange.from,
                last_business_activity_before: businessActivityRange.before,
                ...columnFilters,
                exclude_user_uid: currentUser.uid,
                sort_by: sort.key || "created_at",
                sort_direction: sort.key ? sort.direction : "desc",
            },
        })
            .then(({ data }) => {
                if (!current()) return;
                const lastPage = Math.max(1, Math.ceil(data.total_records / pageLimit));
                if (page > lastPage) {
                    setPage(lastPage);
                    return;
                }
                // Page hydration updates cached models; only external changes invalidate the page.
                applyingPage.current = true;
                try {
                    setRows(User.Model.fromArray(data.records, true));
                } finally {
                    applyingPage.current = false;
                }
                setTotal(data.total_records);
                setSummary(data.account_counts ? { identity: summaryIdentity, counts: data.account_counts } : undefined);
                setFullAccessEmails(data.full_access_emails ?? []);
            })
            .catch(() => {
                if (current()) {
                    setRows([]);
                    setTotal(0);
                    setError(true);
                }
            })
            .finally(() => {
                if (current()) setLoading(false);
            });
        return () => abort.abort();
    }, [
        page,
        search,
        columnFilters,
        sort,
        accountStatus,
        identityProvider,
        signInRange,
        businessActivityRange,
        currentUser.uid,
        sessionVersion,
        summaryIdentity,
        revision,
        setSelectedUsers,
    ]);

    const paginate = (options: IPaginateOptions) => {
        setPage(options.page ?? 1);
        if (options.search !== undefined) setSearch(options.search);
        if (options.columnFilters) setColumnFilters(options.columnFilters);
        if (options.sort) setSort(options.sort);
    };

    return (
        <div aria-busy={loading}>
            {!loading && !error && summary?.identity === summaryIdentity && (
                <section aria-label={t("settings.Account summary")} className="mb-3 space-y-1">
                    <p className="text-xs text-muted-foreground">{t("settings.Account summary scope")}</p>
                    <dl className="flex flex-wrap gap-6 text-sm">
                        {(
                            [
                                ["total", "All accounts"],
                                ["active", "Active"],
                                ["inactive", "Inactive"],
                            ] as const
                        ).map(([key, label]) => (
                            <div key={key}>
                                <dt className="text-muted-foreground">{t(`settings.${label}`)}</dt>
                                <dd className="font-semibold tabular-nums">{summary.counts[key]}</dd>
                            </div>
                        ))}
                    </dl>
                </section>
            )}
            <div className="mb-3 flex flex-wrap items-center gap-3">
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
                <Select.Root
                    value={identityProvider}
                    onValueChange={(value) => {
                        setIdentityProvider(value);
                        setPage(1);
                    }}
                >
                    <Select.Trigger className="w-56" aria-label={t("settings.Identity source")}>
                        <Select.Value />
                    </Select.Trigger>
                    <Select.Content>
                        <Select.Item value="all">{t("settings.All identity sources")}</Select.Item>
                        <Select.Item value="oidc">{t("settings.OIDC linked")}</Select.Item>
                        <Select.Item value="scim">{t("settings.SCIM provisioned")}</Select.Item>
                    </Select.Content>
                </Select.Root>
                <Button variant="outline" disabled={loading} onClick={() => setRevision((value) => value + 1)}>
                    {t("common.Refresh")}
                </Button>
            </div>
            <form
                className="mb-3 flex flex-wrap items-end gap-3"
                onSubmit={(event) => {
                    event.preventDefault();
                    try {
                        const from = localSignInBound(signInFrom);
                        const before = localSignInBound(signInBefore);
                        if (from && before && from >= before) throw new Error("Invalid range");
                        setRangeError(false);
                        setSignInRange({ from, before });
                        setPage(1);
                    } catch {
                        setRangeError(true);
                    }
                }}
            >
                <label>
                    <span className="mb-1 block text-sm">{t("settings.Sign-in from")}</span>
                    <Input type="datetime-local" value={signInFrom} onChange={(event) => setSignInFrom(event.target.value)} />
                </label>
                <label>
                    <span className="mb-1 block text-sm">{t("settings.Sign-in before")}</span>
                    <Input type="datetime-local" value={signInBefore} onChange={(event) => setSignInBefore(event.target.value)} />
                </label>
                <Button type="submit">{t("settings.Apply sign-in range")}</Button>
                <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                        setSignInFrom("");
                        setSignInBefore("");
                        setSignInRange({});
                        setRangeError(false);
                        setPage(1);
                    }}
                >
                    {t("settings.Clear sign-in range")}
                </Button>
            </form>
            <p className="mb-3 text-sm text-muted-foreground">{t("settings.Sign-in range help")}</p>
            {rangeError && (
                <p role="alert" className="mb-3 text-destructive">
                    {t("settings.Invalid sign-in range")}
                </p>
            )}
            <form
                className="mb-3 flex flex-wrap items-end gap-3"
                onSubmit={(event) => {
                    event.preventDefault();
                    try {
                        const from = localSignInBound(businessActivityFrom);
                        const before = localSignInBound(businessActivityBefore);
                        if (from && before && from >= before) throw new Error("Invalid range");
                        setBusinessActivityRangeError(false);
                        setBusinessActivityRange({ from, before });
                        setPage(1);
                    } catch {
                        setBusinessActivityRangeError(true);
                    }
                }}
            >
                <label>
                    <span className="mb-1 block text-sm">{t("settings.Business activity from")}</span>
                    <Input type="datetime-local" value={businessActivityFrom} onChange={(event) => setBusinessActivityFrom(event.target.value)} />
                </label>
                <label>
                    <span className="mb-1 block text-sm">{t("settings.Business activity before")}</span>
                    <Input type="datetime-local" value={businessActivityBefore} onChange={(event) => setBusinessActivityBefore(event.target.value)} />
                </label>
                <Button type="submit">{t("settings.Apply business activity range")}</Button>
                <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                        setBusinessActivityFrom("");
                        setBusinessActivityBefore("");
                        setBusinessActivityRange({});
                        setBusinessActivityRangeError(false);
                        setPage(1);
                    }}
                >
                    {t("settings.Clear business activity range")}
                </Button>
            </form>
            <p className="mb-3 text-sm text-muted-foreground">{t("settings.Business activity range help")}</p>
            {businessActivityRangeError && (
                <p role="alert" className="mb-3 text-destructive">
                    {t("settings.Invalid business activity range")}
                </p>
            )}
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
                        key: "identity_sources",
                        header: t("settings.Identity source"),
                        align: "center",
                        width: "min-w-40",
                        render: ({ row }) => <UserListIdentitySources user={row} />,
                    },
                    {
                        key: "last_signed_in_at",
                        sortable: true,
                        header: t("settings.Last successful sign-in"),
                        align: "center",
                        width: "min-w-40",
                        render: ({ row }) => <UserListItemLastSignIn user={row} />,
                    },
                    {
                        key: "latest_business_activity_at",
                        sortable: true,
                        header: t("settings.Latest business activity"),
                        align: "center",
                        width: "min-w-40",
                        render: ({ row }) => <UserListItemBusinessActivity user={row} />,
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
    const [t] = useTranslation();
    const email = user.useField("email");
    const username = user.useField("username");
    return (
        <div className="flex items-center gap-2 text-left">
            <UserAvatar.Root userOrBot={user} onlyAvatar avatarSize="sm" />
            <div className="min-w-0 break-all">
                <span className="block">{email}</span>
                <span className="block text-xs text-muted-foreground">@{username}</span>
                <span className="block font-mono text-xs text-muted-foreground" aria-label={t("settings.User ID")}>
                    {user.uid}
                </span>
            </div>
        </div>
    );
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

function UserListItemBusinessActivity({ user }: { user: User.TModel }) {
    const [t] = useTranslation();
    const activityAt = user.useField("latest_business_activity_at");
    return activityAt ? (
        <DateDistance date={activityAt} title={activityAt.toISOString()} />
    ) : (
        <span className="text-muted-foreground">{t("settings.No recorded business activity")}</span>
    );
}

export default UserList;
