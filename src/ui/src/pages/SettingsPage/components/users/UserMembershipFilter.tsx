import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import DataTable from "@/components/base/DataTable";
import useGroupDirectory from "@/controllers/api/settings/users/useGroupDirectory";
import { useState } from "react";
import { useTranslation } from "react-i18next";

interface Choice {
    uid: string;
    display_name: string;
}

// The parent keys this selector by current session and current permission identity.
export default function UserMembershipFilter({
    kind,
    value,
    onChange,
}: {
    kind: "group" | "project";
    value: string;
    onChange: (value: string) => void;
}) {
    const [t] = useTranslation();
    const [open, setOpen] = useState(false);
    const [page, setPage] = useState(1);
    const [query, setQuery] = useState("");
    const [selected, setSelected] = useState<Choice>();
    const label = t(kind === "group" ? "settings.Filter by group" : "settings.Filter by participating board");
    const result = useGroupDirectory<Choice>(kind === "group" ? "/settings/groups" : "/settings/users/filter-projects", page, query, open);
    return (
        <section aria-label={label} className="mb-3 space-y-2">
            <Button type="button" variant="outline" aria-expanded={open} onClick={() => setOpen(!open)}>
                {label}
                {value && selected ? `: ${selected.display_name}` : ""}
            </Button>
            {value && (
                <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                        onChange("");
                        setSelected(undefined);
                    }}
                >
                    {t("settings.Clear membership filter")}
                </Button>
            )}
            {open && (
                <div className="space-y-2">
                    <label className="block">
                        {t(kind === "group" ? "settings.Search groups" : "settings.Search boards")}
                        <Input
                            value={query}
                            maxLength={200}
                            onChange={(event) => {
                                setQuery(event.target.value);
                                setPage(1);
                            }}
                        />
                    </label>
                    {result.error ? (
                        <p role="alert">{t("settings.Membership filter unavailable")}</p>
                    ) : (
                        <DataTable<Choice>
                            data={result.data?.records ?? []}
                            totalRecords={result.data?.total_records ?? 0}
                            itemsPerPage={15}
                            loading={result.loading}
                            showPagination
                            remote={{ page, onChange: (options) => setPage(options.page ?? 1) }}
                            columns={[
                                {
                                    key: "display_name",
                                    header: label,
                                    render: ({ row }) => (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            aria-pressed={value === row.uid}
                                            onClick={() => {
                                                setSelected(row);
                                                onChange(row.uid);
                                                setOpen(false);
                                            }}
                                        >
                                            {row.display_name}
                                        </Button>
                                    ),
                                },
                            ]}
                        />
                    )}
                </div>
            )}
        </section>
    );
}
