import Badge from "@/components/base/Badge";
import type { IAdmission } from "@/core/models/botSchedules/BaseBotScheduleModel";
import { scheduleAdmissionReason, scheduleAdmissionStatus } from "@/core/utils/ScheduleAdmissionCopy";
import { useTranslation } from "react-i18next";

function AdmissionTime({ value }: { value: string }): React.JSX.Element | null {
    const { i18n } = useTranslation();
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : <time dateTime={date.toISOString()}>{date.toLocaleString(i18n.resolvedLanguage)}</time>;
}

export default function BotScheduleAdmission({ admission }: { admission?: IAdmission }): React.JSX.Element | null {
    const [t] = useTranslation();
    if (!admission) return null;
    const previous = admission.is_current_occurrence === false;
    return (
        <section className="mt-3 min-w-0 space-y-1.5 rounded-md border p-3 text-sm" aria-label={t("bot.schedules.admission.Title")}>
            <Badge variant={!previous && admission.state === "failed" ? "destructive" : "outline"}>
                {previous ? t("bot.schedules.admission.Previous occurrence") : scheduleAdmissionStatus(t, admission.state)}
            </Badge>
            <p>{t("bot.schedules.admission.Attempts", { count: admission.attempt, max: admission.max_attempts })}</p>
            {!previous && admission.state === "pending" && (
                <p>
                    {t("bot.schedules.admission.Next attempt")}: <AdmissionTime value={admission.next_attempt_at} />
                </p>
            )}
            {!previous && ["pending", "dispatching"].includes(admission.state) && (
                <p>
                    {t("bot.schedules.admission.Expires")}: <AdmissionTime value={admission.expires_at} />
                </p>
            )}
            {!!admission.error_code && (
                <p className="break-words">
                    {t("bot.schedules.admission.Reason")}: {scheduleAdmissionReason(t, admission.error_code)}
                </p>
            )}
            <p className="text-xs text-muted-foreground">{t("bot.schedules.admission.Admission is not completion")}</p>
        </section>
    );
}
