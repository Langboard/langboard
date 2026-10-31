import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import { localSignInBound } from "./userLoginRange";
import Table from "@/components/base/Table";
import DateDistance from "@/components/DateDistance";
import useUserBusinessActivity, {
    BusinessActivityFilters,
    UserBusinessActivityRecord,
} from "@/controllers/api/settings/users/useUserBusinessActivity";
import { ROUTES } from "@/core/routing/constants";
import { useId, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Link } from "react-router";

function ActivityAction({ record }: { record: UserBusinessActivityRecord }) {
    const resource = record.resource;
    const project = (
        <Link className="underline" to={ROUTES.BOARD.MAIN(record.project.uid)}>
            {record.project.title}
        </Link>
    );
    const target = resource ? (
        <Link
            className="underline"
            to={
                resource.type === "wiki"
                    ? ROUTES.BOARD.WIKI_PAGE(record.project.uid, resource.uid)
                    : ROUTES.BOARD.CARD(record.project.uid, resource.uid)
            }
        >
            {resource.title || resource.uid}
        </Link>
    ) : (
        <span>—</span>
    );
    return (
        <Trans
            i18nKey={`activity.project.${record.action}.default`}
            defaults={record.action}
            components={{
                Recorder: <span>{record.actor_principal_uid}</span>,
                Project: project,
                Card: target,
                Wiki: target,
                Column: <span>—</span>,
                FromColumn: <span>—</span>,
                Label: <span>—</span>,
                Attachment: <span>—</span>,
                Checklist: <span>—</span>,
                Checkitem: <span>—</span>,
                CardifiedCard: <span>—</span>,
                SourceCard: <span>—</span>,
            }}
        />
    );
}

function UserBusinessActivity({ uid }: { uid: string }) {
    const [t] = useTranslation();
    const [page, setPage] = useState(1);
    const id = useId();
    const [filters, setFilters] = useState<BusinessActivityFilters>({});
    const [start, setStart] = useState("");
    const [end, setEnd] = useState("");
    const [invalidRange, setInvalidRange] = useState(false);
    const { data, loading, error, retry } = useUserBusinessActivity(uid, page, filters);
    const label = t("settings.Business activity", { defaultValue: "Business activity" });
    return (
        <section aria-label={label} aria-busy={loading} className="space-y-2 border-t pt-4">
            <h3 className="font-semibold">{label}</h3>
            <form
                className="space-y-2"
                onSubmit={(event) => {
                    event.preventDefault();
                    try {
                        const from = localSignInBound(start);
                        const before = localSignInBound(end);
                        if (from && before && from >= before) throw new Error("Invalid activity range");
                        setInvalidRange(false);
                        setPage(1);
                        setFilters({ occurred_from: from, occurred_before: before });
                    } catch {
                        setInvalidRange(true);
                    }
                }}
            >
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
            {error && (
                <div className="space-y-2">
                    <p role="alert">{error === 403 ? t("settings.Directory forbidden") : t("settings.User details failed")}</p>
                    <Button variant="outline" size="sm" onClick={retry}>
                        {t("common.Retry")}
                    </Button>
                </div>
            )}
            {data && (
                <>
                    {!data.records.length ? (
                        <p>{t("activity.No activities")}</p>
                    ) : (
                        <Table.Root aria-label={label}>
                            <Table.Header>
                                <Table.Row>
                                    <Table.Head>{t("settings.Created")}</Table.Head>
                                    <Table.Head>{t("common.Actions")}</Table.Head>
                                </Table.Row>
                            </Table.Header>
                            <Table.Body>
                                {data.records.map((record) => (
                                    <Table.Row key={record.event_id}>
                                        <Table.Cell>
                                            <DateDistance date={new Date(record.occurred_at)} title={new Date(record.occurred_at).toISOString()} />
                                        </Table.Cell>
                                        <Table.Cell className="break-words">
                                            <ActivityAction record={record} />
                                            <div className="text-muted-foreground">
                                                <Link className="underline" to={ROUTES.BOARD.MAIN(record.project.uid)}>
                                                    {record.project.title}
                                                </Link>
                                                {record.resource && (
                                                    <>
                                                        {" "}
                                                        ·{" "}
                                                        <Link
                                                            className="underline"
                                                            to={
                                                                record.resource.type === "wiki"
                                                                    ? ROUTES.BOARD.WIKI_PAGE(record.project.uid, record.resource.uid)
                                                                    : ROUTES.BOARD.CARD(record.project.uid, record.resource.uid)
                                                            }
                                                        >
                                                            {record.resource.title || record.resource.uid}
                                                        </Link>
                                                    </>
                                                )}
                                            </div>
                                        </Table.Cell>
                                    </Table.Row>
                                ))}
                            </Table.Body>
                        </Table.Root>
                    )}
                </>
            )}
            <nav aria-label={label} className="flex items-center justify-between gap-2">
                <Button
                    variant="outline"
                    size="sm"
                    disabled={loading || !!error || page === 1}
                    onClick={() => setPage((value) => Math.max(1, value - 1))}
                >
                    {t("datatable.Previous Page")}
                </Button>
                <span aria-live="polite">{t("datatable.Page {page}", { page })}</span>
                <Button
                    variant="outline"
                    size="sm"
                    disabled={loading || !!error || !data?.has_more || page >= 1000000}
                    onClick={() => setPage((value) => Math.min(1000000, value + 1))}
                >
                    {t("datatable.Next Page")}
                </Button>
            </nav>
        </section>
    );
}

export default UserBusinessActivity;
