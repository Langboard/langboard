import Button from "@/components/base/Button";
import Table from "@/components/base/Table";
import DateDistance from "@/components/DateDistance";
import useUserBusinessActivity, { UserBusinessActivityRecord } from "@/controllers/api/settings/users/useUserBusinessActivity";
import { ROUTES } from "@/core/routing/constants";
import { useState } from "react";
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
    const { data, loading, error, retry } = useUserBusinessActivity(uid, page);
    const label = t("settings.Business activity", { defaultValue: "Business activity" });
    return (
        <section aria-label={label} aria-busy={loading} className="space-y-2 border-t pt-4">
            <h3 className="font-semibold">{label}</h3>
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
