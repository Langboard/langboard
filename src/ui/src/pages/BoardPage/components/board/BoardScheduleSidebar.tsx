import BoardCardWorkState from "./BoardCardWorkState";
import { useEffect, useState } from "react";
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
import useGetScheduleWorkload from "@/controllers/api/dashboard/useGetScheduleWorkload";
import { dayRange, monthRange, parseLocal, validRange } from "./BoardScheduleRange";
import BoardScheduleWorkload from "./BoardScheduleWorkload";

export default function BoardScheduleSidebar({ project, onNavigate }: { project: Project.TModel; onNavigate?: () => void }) {
    const [t, i18n] = useTranslation();
    const navigate = usePageNavigateRef();
    const actions = project.useField("current_auth_role_actions");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const canRead = hasRoleAction(ProjectRole.EAction.Read);
    const [scope, setScope] = useState<"board" | "personal">("board");
    const [view, setView] = useState<"Today" | "Deadlines" | "Workload">("Today");
    const [today, setToday] = useState(() => dayRange(new Date()));
    useEffect(() => {
        let timer: ReturnType<typeof setTimeout>;
        const refresh = () => {
            clearTimeout(timer);
            const now = new Date();
            setToday(dayRange(now));
            timer = setTimeout(refresh, new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime() - now.getTime() + 50);
        };
        refresh();
        document.addEventListener("visibilitychange", refresh);
        return () => {
            clearTimeout(timer);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, []);
    const [range, setRange] = useState(() => monthRange(new Date()));
    const [calendarOpen, setCalendarOpen] = useState(false);
    const activeRange = view === "Today" ? today : range;
    const start = parseLocal(activeRange.start);
    const end = parseLocal(activeRange.end);
    const valid = validRange(start, end);
    const allowed = scope === "personal" || canRead;
    const request = { scope, project_uid: project.uid, start: valid ? start.toISOString() : "", end: valid ? end.toISOString() : "" };
    const schedule = useGetScheduleCalendar(request, valid && allowed && view !== "Workload");
    const workload = useGetScheduleWorkload(request, valid && allowed && view === "Workload");
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
                <div role="group" className="flex flex-wrap gap-1" aria-label={t("dashboard.Schedule view")}>
                    {(["Today", "Deadlines", "Workload"] as const).map((option) => (
                        <Button
                            key={option}
                            size="sm"
                            variant={view === option ? "default" : "outline"}
                            aria-pressed={view === option}
                            onClick={() => setView(option)}
                        >
                            {t(`dashboard.${option}`)}
                        </Button>
                    ))}
                </div>
                {view === "Today" ? (
                    <p className="text-sm">{t("dashboard.Today deadlines", { date: start.toLocaleDateString(i18n.language) })}</p>
                ) : (
                    <>
                        <div className="flex flex-wrap gap-1">
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
                    </>
                )}
                <p className="break-words text-xs text-muted-foreground">
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
                ) : view === "Workload" ? (
                    <BoardScheduleWorkload query={workload} />
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
                        <h3 className="text-xs font-semibold uppercase tracking-wide">{t(`dashboard.${view}`)}</h3>
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
