import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import Select from "@/components/base/Select";
import Table from "@/components/base/Table";
import DateDistance from "@/components/DateDistance";
import useUserSignInHistory, { SignInHistoryFilters } from "@/controllers/api/settings/users/useUserSignInHistory";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { localSignInBound } from "./userLoginRange";

export default function UserSignInHistory({ uid }: { uid: string }) {
    const [t] = useTranslation();
    const id = useId();
    const [page, setPage] = useState(1);
    const [filters, setFilters] = useState<SignInHistoryFilters>({ outcome: "all" });
    const [start, setStart] = useState("");
    const [end, setEnd] = useState("");
    const [invalidRange, setInvalidRange] = useState(false);
    const { data, loading, error, retry } = useUserSignInHistory(uid, page, filters);
    const label = t("settings.Sign-in security history");
    return (
        <section aria-label={label} aria-busy={loading} className="space-y-2 border-t pt-4">
            <h3 className="font-semibold">{label}</h3>
            <p className="text-sm text-muted-foreground">{t("settings.Sign-in history description")}</p>
            <form
                className="space-y-2"
                onSubmit={(event) => {
                    event.preventDefault();
                    let from: string | undefined;
                    let before: string | undefined;
                    try {
                        from = localSignInBound(start);
                        before = localSignInBound(end);
                        if (from && before && from >= before) throw new Error("Invalid audit range");
                    } catch {
                        setInvalidRange(true);
                        return;
                    }
                    setInvalidRange(false);
                    setPage(1);
                    setFilters({ ...filters, occurred_from: from, occurred_before: before });
                }}
            >
                <label htmlFor={`${id}-outcome`}>{t("settings.Sign-in outcome")}</label>
                <Select.Root
                    value={filters.outcome}
                    onValueChange={(value) => {
                        setPage(1);
                        setFilters({ ...filters, outcome: value as SignInHistoryFilters["outcome"] });
                    }}
                >
                    <Select.Trigger id={`${id}-outcome`}>
                        <Select.Value />
                    </Select.Trigger>
                    <Select.Content>
                        <Select.Item value="all">{t("settings.All sign-in outcomes")}</Select.Item>
                        <Select.Item value="success">{t("settings.Sign-in succeeded")}</Select.Item>
                        <Select.Item value="failure">{t("settings.Sign-in failed")}</Select.Item>
                    </Select.Content>
                </Select.Root>
                <label htmlFor={`${id}-from`}>{t("settings.Audit range from")}</label>
                <Input id={`${id}-from`} type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} />
                <label htmlFor={`${id}-before`}>{t("settings.Audit range before")}</label>
                <Input id={`${id}-before`} type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} />
                {invalidRange && <p role="alert">{t("settings.Invalid audit range")}</p>}
                <Button type="submit" variant="outline" size="sm">
                    {t("common.Apply filters")}
                </Button>
            </form>
            {loading && <p role="status">{t("common.Loading...")}</p>}
            {error && <p role="alert">{t(error === 403 ? "settings.Directory forbidden" : "settings.User details failed")}</p>}
            {data &&
                (!data.records.length ? (
                    <p>{t("settings.No sign-in history")}</p>
                ) : (
                    <Table.Root aria-label={label}>
                        <Table.Header>
                            <Table.Row>
                                <Table.Head>{t("settings.Created")}</Table.Head>
                                <Table.Head>{t("settings.Sign-in outcome")}</Table.Head>
                            </Table.Row>
                        </Table.Header>
                        <Table.Body>
                            {data.records.map((record) => (
                                <Table.Row key={record.event_id}>
                                    <Table.Cell>
                                        <DateDistance date={new Date(record.occurred_at)} title={new Date(record.occurred_at).toISOString()} />
                                    </Table.Cell>
                                    <Table.Cell>{t(record.is_success ? "settings.Sign-in succeeded" : "settings.Sign-in failed")}</Table.Cell>
                                </Table.Row>
                            ))}
                        </Table.Body>
                    </Table.Root>
                ))}
            <div className="flex items-center justify-between gap-2">
                <Button variant="outline" size="sm" disabled={loading} onClick={retry}>
                    {t(error ? "common.Retry" : "common.Refresh")}
                </Button>
                <nav aria-label={label} className="flex items-center gap-2">
                    <Button variant="outline" size="sm" disabled={loading || !!error || page === 1} onClick={() => setPage((value) => value - 1)}>
                        {t("datatable.Previous Page")}
                    </Button>
                    <span aria-live="polite">{t("datatable.Page {page}", { page })}</span>
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={loading || !!error || !data?.has_more || page >= 1000000}
                        onClick={() => setPage((value) => value + 1)}
                    >
                        {t("datatable.Next Page")}
                    </Button>
                </nav>
            </div>
        </section>
    );
}
