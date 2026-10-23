import Button from "@/components/base/Button";
import DataTable from "@/components/base/DataTable";
import Input from "@/components/base/Input";
import { IPaginateOptions } from "@/components/base/DataTable/Provider";
import useGroupDirectory, { DirectoryGroup, DirectoryMember } from "@/controllers/api/settings/users/useGroupDirectory";
import SettingsLoadState from "@/pages/SettingsPage/SettingsLoadState";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export default function GroupDirectory() {
    const [t] = useTranslation();
    const [page, setPage] = useState(1);
    const [query, setQuery] = useState("");
    const [selected, setSelected] = useState<DirectoryGroup>();
    const result = useGroupDirectory<DirectoryGroup>("/settings/groups", page, query);
    const paginate = (options: IPaginateOptions) => setPage(options.page ?? 1);
    return (
        <section aria-label={t("settings.Groups")} aria-busy={result.loading} className="space-y-4">
            <h2 className="text-xl font-semibold">{t("settings.Groups")}</h2>
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
                                    {t("settings.Read only")}
                                    {row.managed ? ` · ${t("settings.Externally managed")}` : ""}
                                </span>
                            ),
                        },
                    ]}
                />
            )}
            {selected && !result.error && <GroupMembers key={selected.uid} group={selected} />}
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

function GroupMembers({ group }: { group: DirectoryGroup }) {
    const [t] = useTranslation();
    const [page, setPage] = useState(1);
    const [query, setQuery] = useState("");
    const result = useGroupDirectory<DirectoryMember>(`/settings/groups/${group.uid}/members`, page, query);
    return (
        <section
            aria-label={`${t("settings.Members")}: ${group.display_name}`}
            aria-busy={result.loading}
            className="space-y-4 rounded-lg border p-4"
        >
            <h3 className="text-lg font-semibold">
                {t("settings.Members")}: {group.display_name}
            </h3>
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
                        { key: "uid", header: t("settings.User ID") },
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
