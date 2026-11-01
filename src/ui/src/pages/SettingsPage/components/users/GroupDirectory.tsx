import Button from "@/components/base/Button";
import DataTable from "@/components/base/DataTable";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";
import { isAxiosError } from "axios";
import Input from "@/components/base/Input";
import { IPaginateOptions } from "@/components/base/DataTable/Provider";
import useGroupDirectory, { DirectoryGroup, DirectoryMember, DirectoryHistory } from "@/controllers/api/settings/users/useGroupDirectory";
import SettingsLoadState from "@/pages/SettingsPage/SettingsLoadState";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export default function GroupDirectory() {
    const { currentUser } = useAppSetting();
    const actions = currentUser.useField("setting_role_actions");
    const isAdmin = currentUser.useField("is_admin");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const canRead = Boolean(isAdmin) && hasRoleAction(SettingRole.EAction.GroupRead);
    const canManage = canRead && hasRoleAction(SettingRole.EAction.GroupManage);
    if (!canRead) return <DeniedDirectory />;
    return <DirectoryContent key={`${currentUser.uid}:${canManage}`} canManage={canManage} />;
}

function DeniedDirectory() {
    const [t] = useTranslation();
    return <p role="alert">{t("settings.Directory forbidden")}</p>;
}

function DirectoryContent({ canManage }: { canManage: boolean }) {
    const [t] = useTranslation();
    const [page, setPage] = useState(1);
    const [query, setQuery] = useState("");
    const [selected, setSelected] = useState<DirectoryGroup>();
    const [creating, setCreating] = useState(false);
    const result = useGroupDirectory<DirectoryGroup>("/settings/groups", page, query);
    const paginate = (options: IPaginateOptions) => setPage(options.page ?? 1);
    return (
        <section aria-label={t("settings.Groups")} aria-busy={result.loading} className="space-y-4">
            <h2 className="text-xl font-semibold">{t("settings.Groups")}</h2>
            {canManage && <Button onClick={() => setCreating(true)}>{t("settings.Create group")}</Button>}
            {creating && <GroupMutation action="create" close={() => setCreating(false)} refresh={result.retry} />}
            <label className="block">
                {t("settings.Search groups")}
                <Input
                    maxLength={200}
                    value={query}
                    onChange={(event) => {
                        setQuery(event.target.value);
                        setPage(1);
                        setSelected(undefined);
                    }}
                />
            </label>
            <DirectoryState result={result} />
            {!result.error && (
                <DataTable<DirectoryGroup>
                    data={result.data?.records ?? []}
                    totalRecords={result.data?.total_records ?? 0}
                    itemsPerPage={15}
                    loading={result.loading}
                    showPagination
                    remote={{ page, onChange: paginate }}
                    columns={[
                        {
                            key: "display_name",
                            header: t("settings.Groups"),
                            render: ({ row }) => (
                                <Button
                                    variant={row.uid === selected?.uid ? "secondary" : "ghost"}
                                    aria-pressed={row.uid === selected?.uid}
                                    disabled={!row.abilities.read_members}
                                    onClick={() => setSelected(row)}
                                >
                                    {row.display_name}
                                </Button>
                            ),
                        },
                        { key: "source", header: t("settings.Source"), render: ({ row }) => <GroupSource group={row} /> },
                        {
                            key: "issuer",
                            header: t("settings.SCIM issuer"),
                            render: ({ row }) => <span className="break-all">{row.issuer || "—"}</span>,
                        },
                        { key: "external_id", header: t("settings.External group ID") },
                        {
                            key: "read_only",
                            header: t("settings.Management"),
                            render: ({ row }) => (
                                <span>
                                    {t(canManage && isLocalWritable(row) ? "settings.Local management" : "settings.Read only")}
                                    {row.managed ? ` · ${t("settings.Externally managed")}` : ""}
                                </span>
                            ),
                        },
                    ]}
                />
            )}
            {selected && !result.error && (
                <GroupMembers
                    key={selected.uid}
                    group={selected}
                    canManage={canManage}
                    refreshDirectory={result.retry}
                    removed={() => setSelected(undefined)}
                />
            )}
        </section>
    );
}

function GroupSource({ group }: { group: DirectoryGroup }) {
    const [t] = useTranslation();
    const source = group.source === "scim" ? "SCIM" : group.source === "local" ? "Local" : "Unresolved";
    const authority =
        group.source_status === "current_authority" ? "Current authority" : group.source_status === "other_authority" ? "Other authority" : undefined;
    return (
        <span>
            {t(`settings.${source}`)}
            {authority ? ` · ${t(`settings.${authority}`)}` : ""}
        </span>
    );
}

function DirectoryState({ result }: { result: { loading: boolean; error?: number; retry: () => void } }) {
    const [t] = useTranslation();
    return result.error === 403 ? (
        <div role="alert">
            <p>{t("settings.Directory forbidden")}</p>
            <Button variant="outline" onClick={result.retry}>
                {t("common.Retry")}
            </Button>
        </div>
    ) : result.loading || result.error ? (
        <SettingsLoadState error={Boolean(result.error)} isFetching={result.loading} retry={result.retry} />
    ) : null;
}

function isLocalWritable(group: DirectoryGroup) {
    return group.source === "local" && !group.managed && !group.read_only;
}

function GroupMembers({
    group,
    canManage,
    refreshDirectory,
    removed,
}: {
    group: DirectoryGroup;
    canManage: boolean;
    refreshDirectory: () => void;
    removed: () => void;
}) {
    const [t] = useTranslation();
    const [page, setPage] = useState(1);
    const [query, setQuery] = useState("");
    const result = useGroupDirectory<DirectoryMember>(`/settings/groups/${group.uid}/members`, page, query);
    const [action, setAction] = useState<"rename" | "delete" | "add" | "remove">();
    const [member, setMember] = useState<DirectoryMember>();
    const [history, setHistory] = useState(false);
    const [historyVersion, setHistoryVersion] = useState(0);
    const current = result.data?.group;
    const writable = canManage && current && isLocalWritable(current) && Number.isInteger(current.revision);
    const refresh = () => {
        result.retry();
        refreshDirectory();
        setHistoryVersion((value) => value + 1);
    };
    return (
        <section
            aria-label={`${t("settings.Members")}: ${current?.display_name ?? group.display_name}`}
            aria-busy={result.loading}
            className="space-y-4 rounded-lg border p-4"
        >
            <h3 className="text-lg font-semibold">
                {t("settings.Members")}: {current?.display_name ?? group.display_name}
            </h3>
            <div className="flex flex-wrap gap-2">
                {writable && current.abilities.update && <Button onClick={() => setAction("rename")}>{t("settings.Rename group")}</Button>}
                {writable && current.abilities.delete && (
                    <Button variant="destructive" onClick={() => setAction("delete")}>
                        {t("settings.Delete group")}
                    </Button>
                )}
                {writable && current.abilities.manage_members && <Button onClick={() => setAction("add")}>{t("settings.Add members")}</Button>}
                <Button variant="outline" onClick={() => setHistory((value) => !value)}>
                    {t("settings.Group history")}
                </Button>
            </div>
            {action && (
                <GroupMutation
                    action={action}
                    ready={Boolean(writable && current.abilities[action === "rename" ? "update" : action === "delete" ? "delete" : "manage_members"])}
                    group={current ?? group}
                    member={member}
                    close={() => setAction(undefined)}
                    refresh={refresh}
                    removed={removed}
                />
            )}
            {history && <GroupHistory key={historyVersion} group={group} />}
            <label className="block">
                {t("settings.Search members")}
                <Input
                    maxLength={200}
                    value={query}
                    onChange={(event) => {
                        setQuery(event.target.value);
                        setPage(1);
                    }}
                />
            </label>
            <DirectoryState result={result} />
            {!result.error && (
                <DataTable<DirectoryMember>
                    data={result.data?.records ?? []}
                    totalRecords={result.data?.total_records ?? 0}
                    itemsPerPage={15}
                    loading={result.loading}
                    showPagination
                    remote={{ page, onChange: (options) => setPage(options.page ?? 1) }}
                    columns={[
                        {
                            key: "uid",
                            header: t("settings.User ID"),
                            render: ({ row }) => (
                                <div className="space-y-2">
                                    <span>{row.uid}</span>
                                    {writable && current.abilities.manage_members && (
                                        <Button
                                            variant="destructive"
                                            onClick={() => {
                                                setMember(row);
                                                setAction("remove");
                                            }}
                                        >
                                            {t("settings.Remove member")}
                                        </Button>
                                    )}
                                </div>
                            ),
                        },
                        { key: "firstname", header: t("user.First Name") },
                        { key: "lastname", header: t("user.Last Name") },
                        { key: "username", header: t("user.Username") },
                        {
                            key: "account_status",
                            header: t("settings.Account status"),
                            render: ({ row }) =>
                                t(
                                    `settings.${row.account_status === "active" ? "Active" : row.account_status === "inactive" ? "Inactive" : "Deleted"}`
                                ),
                        },
                    ]}
                />
            )}
        </section>
    );
}

function GroupHistory({ group }: { group: DirectoryGroup }) {
    const [t] = useTranslation();
    const [page, setPage] = useState(1);
    const result = useGroupDirectory<DirectoryHistory>(`/settings/groups/${group.uid}/history`, page, "");
    return (
        <section aria-label={t("settings.Group history")} className="space-y-2">
            <h4>{t("settings.Group history")}</h4>
            <DirectoryState result={result} />
            {!result.error && (
                <DataTable<DirectoryHistory>
                    data={result.data?.records ?? []}
                    totalRecords={result.data?.total_records ?? 0}
                    itemsPerPage={15}
                    loading={result.loading}
                    showPagination
                    remote={{ page, onChange: (options) => setPage(options.page ?? 1) }}
                    columns={[
                        { key: "actor_uid", header: t("settings.Actor ID") },
                        {
                            key: "action",
                            header: t("settings.Action"),
                            render: ({ row }) => {
                                const labels: Record<string, string> = {
                                    create: "Create group",
                                    rename: "Rename group",
                                    delete: "Delete group",
                                    add_members: "Add members",
                                    remove_members: "Remove member",
                                };
                                return t(`settings.${labels[row.action] ?? "Other group change"}`);
                            },
                        },
                        { key: "revision", header: t("settings.Revision") },
                        {
                            key: "changes",
                            header: t("settings.Changed facts"),
                            render: ({ row }) => {
                                const name = row.changes.display_name;
                                const names = name && typeof name === "object" ? (name as Record<string, unknown>) : undefined;
                                const members = row.changes.user_uids;
                                return (
                                    <span className="break-all">
                                        {typeof name === "string"
                                            ? `${t("settings.Group name")}: ${name}`
                                            : names && typeof names.before === "string" && typeof names.after === "string"
                                              ? `${t("settings.Group name")}: ${names.before} → ${names.after}`
                                              : Array.isArray(members) && members.every((uid) => typeof uid === "string")
                                                ? `${t("settings.User ID")}: ${members.join(", ")}`
                                                : "—"}
                                    </span>
                                );
                            },
                        },
                        { key: "created_at", header: t("settings.Recorded at") },
                    ]}
                />
            )}
        </section>
    );
}

type GroupAction = "create" | "rename" | "delete" | "add" | "remove";
const actionLabels = { create: "Create group", rename: "Rename group", delete: "Delete group", add: "Add members", remove: "Remove member" };

function GroupMutation({
    action,
    group,
    member,
    close,
    refresh,
    removed,
    ready = true,
}: {
    action: GroupAction;
    group?: DirectoryGroup;
    member?: DirectoryMember;
    close: () => void;
    refresh: () => void;
    removed?: () => void;
    ready?: boolean;
}) {
    const [t] = useTranslation();
    const [name, setName] = useState(action === "rename" ? (group?.display_name ?? "") : "");
    const [query, setQuery] = useState("");
    const [page, setPage] = useState(1);
    const [selected, setSelected] = useState<string[]>([]);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<number>();
    const request = useRef<AbortController | undefined>(undefined);
    const returnFocus = useRef(document.activeElement as HTMLElement | null);
    useEffect(() => () => request.current?.abort(), []);
    const candidates = useGroupDirectory<DirectoryMember>("/settings/groups/candidates", page, query, action === "add");
    const requiresName = action === "create" || action === "rename";
    const submit = async () => {
        if (pending || !ready || (requiresName && !name.trim()) || (action === "add" && !selected.length)) return;
        if (action !== "create" && (!group || !isLocalWritable(group) || !Number.isInteger(group.revision))) return;
        const abort = new AbortController();
        request.current = abort;
        setPending(true);
        setError(undefined);
        const path = `/settings/groups${group ? `/${group.uid}` : ""}${action === "add" || action === "remove" ? "/members" : ""}`;
        const method = action === "delete" || action === "remove" ? "DELETE" : action === "rename" ? "PUT" : "POST";
        const data = {
            ...(group ? { expected_revision: group.revision } : {}),
            ...(requiresName ? { display_name: name.trim() } : {}),
            ...(action === "add" ? { user_uids: selected } : action === "remove" ? { user_uids: [member!.uid] } : {}),
        };
        try {
            await api.request({ method, url: path, data, signal: abort.signal, env: { interceptToast: true } as never });
            if (abort.signal.aborted) return;
            refresh();
            if (action === "delete") removed?.();
            close();
        } catch (failure) {
            if (abort.signal.aborted) return;
            const status = isAxiosError(failure) ? failure.response?.status || 500 : 500;
            setError(status);
            if ([403, 404, 409].includes(status)) refresh();
        } finally {
            if (!abort.signal.aborted) setPending(false);
        }
    };
    const message =
        error === 409
            ? "Group changed review current state"
            : error === 403
              ? "Group write denied"
              : error === 404
                ? "Group no longer exists"
                : error === 400
                  ? "Invalid group input"
                  : "Group write failed";
    return (
        <Dialog.Root
            open
            onOpenChange={(open) => {
                if (!open && !pending) close();
            }}
        >
            <Dialog.Content
                withCloseButton={!pending}
                onCloseAutoFocus={(event) => {
                    event.preventDefault();
                    if (returnFocus.current?.isConnected) returnFocus.current.focus();
                }}
                onEscapeKeyDown={(event) => {
                    if (pending) event.preventDefault();
                }}
            >
                <Dialog.Header>
                    <Dialog.Title>{t(`settings.${actionLabels[action]}`)}</Dialog.Title>
                    <Dialog.Description>
                        {t(action === "delete" || action === "remove" ? "settings.Confirm group destructive change" : "settings.Review group change")}
                    </Dialog.Description>
                </Dialog.Header>
                <form
                    className="space-y-4 pt-4"
                    aria-busy={pending}
                    onSubmit={(event) => {
                        event.preventDefault();
                        void submit();
                    }}
                >
                    {group && (
                        <p className="break-all">
                            {group.display_name} · {t("settings.Revision")}: {group.revision}
                        </p>
                    )}
                    {member && (
                        <p className="break-all">
                            {t("settings.User ID")}: {member.uid}
                        </p>
                    )}
                    {requiresName && (
                        <label className="block">
                            {t("settings.Group name")}
                            <Input
                                autoFocus
                                required
                                maxLength={200}
                                value={name}
                                disabled={pending}
                                onChange={(event) => setName(event.target.value)}
                            />
                        </label>
                    )}
                    {action === "add" && (
                        <>
                            <label className="block">
                                {t("settings.Search candidates")}
                                <Input
                                    autoFocus
                                    maxLength={200}
                                    value={query}
                                    disabled={pending}
                                    onChange={(event) => {
                                        setQuery(event.target.value);
                                        setPage(1);
                                    }}
                                />
                            </label>
                            <DirectoryState result={candidates} />
                            {!candidates.error && (
                                <DataTable<DirectoryMember>
                                    data={candidates.data?.records ?? []}
                                    totalRecords={candidates.data?.total_records ?? 0}
                                    loading={candidates.loading}
                                    itemsPerPage={15}
                                    showPagination
                                    remote={{ page, onChange: (options) => setPage(options.page ?? 1) }}
                                    columns={[
                                        {
                                            key: "uid",
                                            header: t("settings.User ID"),
                                            render: ({ row }) => (
                                                <label className="flex gap-2 break-all">
                                                    <input
                                                        type="checkbox"
                                                        disabled={
                                                            pending ||
                                                            row.account_status !== "active" ||
                                                            (selected.length >= 100 && !selected.includes(row.uid))
                                                        }
                                                        checked={selected.includes(row.uid)}
                                                        onChange={(event) =>
                                                            setSelected((values) =>
                                                                event.target.checked ? [...values, row.uid] : values.filter((uid) => uid !== row.uid)
                                                            )
                                                        }
                                                    />
                                                    {row.uid}
                                                </label>
                                            ),
                                        },
                                        { key: "username", header: t("user.Username") },
                                    ]}
                                />
                            )}
                            <p>
                                {t("settings.Selected members")}: {selected.length}
                            </p>
                        </>
                    )}
                    {error && <p role="alert">{t(`settings.${message}`)}</p>}
                    <Dialog.Footer>
                        <Button type="button" variant="outline" disabled={pending} onClick={close}>
                            {t("common.Cancel")}
                        </Button>
                        <Button
                            type="submit"
                            variant={action === "delete" || action === "remove" ? "destructive" : "default"}
                            disabled={pending || !ready || (requiresName && !name.trim()) || (action === "add" && !selected.length)}
                        >
                            {t(pending ? "settings.Saving group change" : `settings.${actionLabels[action]}`)}
                        </Button>
                    </Dialog.Footer>
                </form>
            </Dialog.Content>
        </Dialog.Root>
    );
}
