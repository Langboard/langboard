import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import useGetScheduleWorkload from "@/controllers/api/dashboard/useGetScheduleWorkload";
import { formatDateTime } from "@/core/utils/LocaleFormat";

export default function BoardScheduleWorkload({ query }: { query: ReturnType<typeof useGetScheduleWorkload> }) {
    const [t, i18n] = useTranslation();
    const data = query.data;
    const number = (value: number) => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }).format(value);
    const complete = data?.time.evidence === "complete" && data.time.invested_seconds !== null && data.time.running_item_count !== null;
    return (
        <section aria-label={t("dashboard.Workload")} aria-busy={query.isFetching} className="min-w-0 space-y-3 break-words text-sm">
            <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
                {t(query.isError ? "dashboard.Retry" : "dashboard.Refresh workload")}
            </Button>
            {query.isError && <p role="alert">{t("dashboard.Workload load failed")}</p>}
            {query.isFetching && <p role="status">{t("dashboard.Loading workload")}</p>}
            {data && (
                <>
                    <h3 className="font-semibold">{t("dashboard.Current workload")}</h3>
                    <p className="text-xs text-muted-foreground">{t("dashboard.Workload snapshot explanation")}</p>
                    <p>
                        <time dateTime={data.as_of}>
                            {t("dashboard.Snapshot as of", { date: formatDateTime(new Date(data.as_of), i18n.language) })}
                        </time>
                    </p>
                    <dl className="space-y-2">
                        <div>
                            <dt>{t("dashboard.Open cards")}</dt>
                            <dd>{number(data.workload.open_count)}</dd>
                        </div>
                        <div>
                            <dt>{t("dashboard.Incomplete cards")}</dt>
                            <dd>{number(data.workload.incomplete_count)}</dd>
                        </div>
                    </dl>
                    <h3 className="font-semibold">{t("dashboard.Recorded time in range")}</h3>
                    <p className="text-xs text-muted-foreground">{t("dashboard.All work attribution")}</p>
                    <p className="text-xs text-muted-foreground">{t("dashboard.Time includes archived")}</p>
                    <dl className="space-y-2">
                        <div>
                            <dt>{t("dashboard.Recorded seconds")}</dt>
                            <dd>{complete ? number(data.time.invested_seconds!) : t("dashboard.Unavailable")}</dd>
                        </div>
                        <div>
                            <dt>{t("dashboard.Running items in range")}</dt>
                            <dd>{complete ? number(data.time.running_item_count!) : t("dashboard.Unavailable")}</dd>
                        </div>
                        <div>
                            <dt>{t("dashboard.Items with time evidence")}</dt>
                            <dd>{number(data.time.item_count)}</dd>
                        </div>
                    </dl>
                    {!complete && (
                        <div role="status" className="space-y-1">
                            <p>{t("dashboard.Incomplete time evidence")}</p>
                            <p>{t("dashboard.Known partial seconds", { seconds: number(data.time.known_invested_seconds) })}</p>
                            <p>{t("dashboard.Unknown time items", { count: data.time.unknown_item_count })}</p>
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
