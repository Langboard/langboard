import BoardCardWorkState from "./BoardCardWorkState";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import Calendar from "@/components/base/Calendar";
import Popover from "@/components/base/Popover";
import { Project } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import useGetScheduleCalendar from "@/controllers/api/dashboard/useGetScheduleCalendar";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { formatDateTime } from "@/core/utils/LocaleFormat";

const localDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const monthRange = (date: Date) => ({
    start: localDate(new Date(date.getFullYear(), date.getMonth(), 1)),
    end: localDate(new Date(date.getFullYear(), date.getMonth() + 1, 1)),
});
const parseLocal = (value: string) => {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
};

export default function BoardScheduleSidebar({ project, onNavigate }: { project: Project.TModel; onNavigate?: () => void }) {
    const [t, i18n] = useTranslation();
    const navigate = usePageNavigateRef();
    const actions = project.useField("current_auth_role_actions");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const canRead = hasRoleAction(ProjectRole.EAction.Read);
    const [scope, setScope] = useState<"board" | "personal">("board");
    const [range, setRange] = useState(() => monthRange(new Date()));
    const [calendarOpen, setCalendarOpen] = useState(false);
    const start = parseLocal(range.start);
    const end = parseLocal(range.end);
    const valid = Number.isFinite(start.getTime()) && Number.isFinite(end.getTime()) && end > start;
    const allowed = scope === "personal" || canRead;
    const schedule = useGetScheduleCalendar(
        { scope, project_uid: project.uid, start: valid ? start.toISOString() : "", end: valid ? end.toISOString() : "" },
        valid && allowed
    );
    return (
        <nav aria-label={t("dashboard.Schedule")} data-workbench-context="" className="flex h-full w-full min-w-0 flex-col overflow-hidden">
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
                <div className="flex flex-wrap gap-1" aria-label={t("dashboard.Schedule scope")}>
                    <Button
                        size="sm"
                        variant={scope === "board" ? "default" : "outline"}
                        disabled={!canRead}
                        aria-pressed={scope === "board"}
                        onClick={() => setScope("board")}
                    >
                        {t("dashboard.Board schedule")}
                    </Button>
                    <Button
                        size="sm"
                        variant={scope === "personal" ? "default" : "outline"}
                        aria-pressed={scope === "personal"}
                        onClick={() => setScope("personal")}
                    >
                        {t("dashboard.Personal schedule")}
                    </Button>
                </div>
                <div className="flex flex-wrap gap-1">
                    <Button size="sm" variant="outline" onClick={() => setRange(monthRange(new Date()))}>
                        {t("dashboard.Today")}
                    </Button>
                    <Popover.Root open={calendarOpen} onOpenChange={setCalendarOpen}>
                        <Popover.Trigger asChild>
                            <Button size="sm" variant="outline">
                                {t("dashboard.Choose month")}
                            </Button>
                        </Popover.Trigger>
                        <Popover.Content className="w-auto max-w-[calc(100vw-2rem)] p-2" side="bottom">
                            <Calendar
                                hideTime
                                value={valid ? start : new Date()}
                                onChange={(date) => {
                                    if (date) setRange(monthRange(date));
                                    setCalendarOpen(false);
                                }}
                            />
                        </Popover.Content>
                    </Popover.Root>
                </div>
                <label className="block min-w-0 text-xs">
                    {t("dashboard.Start date")}
                    <Input
                        className="mt-1 w-full min-w-0"
                        type="date"
                        value={range.start}
                        onChange={(event) => setRange({ ...range, start: event.target.value })}
                    />
                </label>
                <label className="block min-w-0 text-xs">
                    {t("dashboard.End date exclusive")}
                    <Input
                        className="mt-1 w-full min-w-0"
                        type="date"
                        value={range.end}
                        onChange={(event) => setRange({ ...range, end: event.target.value })}
                    />
                </label>
                <p className="text-xs text-muted-foreground">
                    {t("dashboard.Schedule timezone", { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })}
                </p>
                {!valid ? (
                    <p role="alert" className="text-sm">
                        {t("dashboard.Invalid schedule range")}
                    </p>
                ) : !allowed ? (
                    <p role="alert" className="text-sm">
                        {t("dashboard.Schedule permission required")}
                    </p>
                ) : (
                    <>
                        <Button
                            className="max-w-full whitespace-normal"
                            size="sm"
                            variant="outline"
                            disabled={schedule.exporting}
                            onClick={() => void schedule.download()}
                        >
                            {t(schedule.exporting ? "dashboard.Exporting calendar" : "dashboard.Download iCal")}
                        </Button>
                        {schedule.exportError && (
                            <p role="alert" className="text-sm">
                                {t("dashboard.Calendar export failed")}
                            </p>
                        )}
                        <h3 className="text-xs font-semibold uppercase tracking-wide">{t("dashboard.Deadlines")}</h3>
                        {schedule.isError && (
                            <div role="alert" className="space-y-2 text-sm">
                                <p>{t("dashboard.Schedule load failed")}</p>
                                <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={schedule.isFetching}
                                    onClick={() => void (schedule.isFetchNextPageError ? schedule.fetchNextPage() : schedule.refetch())}
                                >
                                    {t("dashboard.Retry")}
                                </Button>
                            </div>
                        )}
                        {schedule.isPending ? (
                            <p role="status" className="text-sm">
                                {t("dashboard.Loading schedule")}
                            </p>
                        ) : !schedule.isError && !schedule.items.length ? (
                            <p className="text-sm text-muted-foreground">{t("dashboard.No deadlines in range")}</p>
                        ) : null}
                        <ul className="space-y-1">
                            {schedule.items.map((item) => (
                                <li key={item.card_uid}>
                                    <button
                                        type="button"
                                        className={
                                            "w-full min-w-0 rounded p-2 text-left text-sm hover:bg-muted " +
                                            "focus-visible:outline focus-visible:outline-2"
                                        }
                                        onClick={() => {
                                            navigate(ROUTES.BOARD.CARD(item.project_uid, item.card_uid));
                                            onNavigate?.();
                                        }}
                                    >
                                        <span className="block break-words font-medium">{item.title}</span>
                                        <span className="block break-words text-xs text-muted-foreground">
                                            {item.project_title} · {item.column_name}
                                        </span>
                                        <time className="block text-xs" dateTime={item.deadline_at}>
                                            {formatDateTime(new Date(item.deadline_at), i18n.language)}
                                        </time>
                                        <BoardCardWorkState state={item.work_state} />
                                    </button>
                                </li>
                            ))}
                        </ul>
                        {schedule.hasNextPage && (
                            <Button size="sm" variant="outline" disabled={schedule.isFetching} onClick={() => void schedule.fetchNextPage()}>
                                {t("dashboard.Load more")}
                            </Button>
                        )}
                    </>
                )}
            </div>
        </nav>
    );
}
