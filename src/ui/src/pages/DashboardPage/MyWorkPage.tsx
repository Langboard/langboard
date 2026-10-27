import { useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { ROUTES } from "@/core/routing/constants";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { usePageSearchParams } from "@/core/hooks/usePageSearchParams";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import Button from "@/components/base/Button";
import DateDistance from "@/components/DateDistance";
import { cn } from "@/core/utils/ComponentUtils";
import { formatDateTime } from "@/core/utils/LocaleFormat";
import useAuthStore from "@/core/stores/AuthStore";
import MyWorkCardActions from "./my-work-context/MyWorkCardActions";
import useMyWorkContext from "./my-work-context/useMyWorkContext";
import { isMyWorkGroup, MY_WORK_GROUPS, MY_WORK_LABELS, type MyWorkFilter, type MyWorkOrder } from "./my-work-context/MyWorkContext";

export default function MyWorkPage({
    projectUID: contextProjectUID,
    compact = false,
    order = "updated",
    filter: workspaceFilter,
    onFilterChange,
    onClear,
    onNavigate,
}: {
    projectUID?: string;
    compact?: boolean;
    order?: MyWorkOrder;
    filter?: MyWorkFilter;
    onFilterChange?: (filter: MyWorkFilter) => void;
    onClear?: () => void;
    onNavigate?: () => void;
} = {}) {
    const [t, i18n] = useTranslation();
    const panelRef = useRef<HTMLElement>(null);
    useEffect(() => {
        if (compact) panelRef.current?.focus({ preventScroll: true });
    }, [compact]);
    const navigate = usePageNavigateRef();
    const [searchParams, setSearchParams] = usePageSearchParams();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const session = useAuthStore((state) => state.getSessionVersion());
    const [panelFilter, setPanelFilter] = useState<MyWorkFilter>({ projectUID: contextProjectUID ?? null, group: null });
    useEffect(() => {
        setPanelFilter({ projectUID: contextProjectUID ?? null, group: null });
    }, [contextProjectUID, actorUID]);
    const queryGroup = searchParams.get("group");
    const filter =
        workspaceFilter ??
        (compact
            ? panelFilter
            : {
                  projectUID: searchParams.get("scope") === "all" ? null : searchParams.get("project_uid"),
                  group: isMyWorkGroup(queryGroup) ? queryGroup : null,
              });
    const projectUID = filter.projectUID ?? contextProjectUID ?? searchParams.get("project_uid");
    const { data: projectsData } = useGetProjects({ enabled: !!actorUID });
    const query = useMyWorkContext(filter, order);
    const denied = isAxiosError(query.error) && [401, 403, 404].includes(query.error.response?.status ?? 0);
    // Earlier authorized pages remain usable when only the next page failed transiently.
    const pages = actorUID && !denied && (!query.isError || query.isFetchNextPageError) ? query.data?.pages : undefined;
    const first = pages?.[0];
    const cards = pages?.flatMap((page) => page.cards) ?? [];
    const updateFilter = (next: MyWorkFilter) => {
        if (compact || onFilterChange) {
            setPanelFilter(next);
            onFilterChange?.(next);
            return;
        }
        const params = new URLSearchParams(searchParams);
        if (next.projectUID) params.set("project_uid", next.projectUID);
        if (next.projectUID) params.delete("scope");
        else params.set("scope", "all");
        if (next.group) params.set("group", next.group);
        else params.delete("group");
        setSearchParams(params);
    };
    const title = t(order === "priority" ? "dashboard.Now work" : "dashboard.My Work");
    return (
        <section
            ref={panelRef}
            data-my-work-context={compact ? "" : undefined}
            data-my-work-results={!compact && workspaceFilter ? "" : undefined}
            tabIndex={compact ? -1 : undefined}
            className={cn("mx-auto min-w-0 space-y-4", compact ? "p-3" : "w-full max-w-5xl p-4")}
            aria-label={title}
        >
            <div className="flex flex-wrap items-center gap-2">
                <h1 className={cn("mr-auto font-semibold", compact ? "w-full text-sm" : "text-xl")}>{title}</h1>
                {onClear && (
                    <Button size="sm" variant="outline" onClick={onClear}>
                        {t("board.filters.Clear")}
                    </Button>
                )}
                <Button
                    size="sm"
                    variant={!filter.projectUID ? "secondary" : "outline"}
                    aria-pressed={!filter.projectUID}
                    onClick={() => updateFilter({ ...filter, projectUID: null })}
                >
                    {t("dashboard.All projects")}
                </Button>
                <Button
                    size="sm"
                    variant={filter.projectUID ? "secondary" : "outline"}
                    aria-pressed={!!filter.projectUID}
                    disabled={!projectUID}
                    onClick={() => updateFilter({ ...filter, projectUID })}
                >
                    {t("dashboard.Current project")}
                </Button>
                <select
                    aria-label={t("dashboard.Choose project")}
                    className="h-9 max-w-full rounded-md border bg-background px-2 text-sm sm:max-w-48"
                    value={filter.projectUID ?? ""}
                    onChange={(event) => updateFilter({ ...filter, projectUID: event.target.value || null })}
                >
                    <option value="">{t("dashboard.Choose project")}</option>
                    {projectsData?.projects.map((project) => (
                        <option key={project.uid} value={project.uid}>
                            {project.title}
                        </option>
                    ))}
                </select>
            </div>
            {order === "priority" && <p className="text-xs text-muted-foreground">{t("dashboard.Work priority order")}</p>}
            {actorUID && query.isError && (
                <div role="alert" className="space-x-2">
                    <span>{denied ? t("errors.Forbidden") : t("dashboard.Could not load work")}</span>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={query.isFetching}
                        onClick={() => void (query.isFetchNextPageError && !denied ? query.fetchNextPage() : query.refetch())}
                    >
                        {t("dashboard.Retry")}
                    </Button>
                </div>
            )}
            {!actorUID ? (
                <p role="status">{t("dashboard.Sign in to view work", { defaultValue: "Sign in to view work" })}</p>
            ) : !first ? (
                query.isError ? null : (
                    <p role="status">{t("dashboard.Loading work")}</p>
                )
            ) : (
                <>
                    <nav aria-label={t("dashboard.Work groups", { defaultValue: "Work groups" })} className="flex flex-wrap gap-1">
                        <Button
                            size="sm"
                            variant={!filter.group ? "secondary" : "ghost"}
                            aria-pressed={!filter.group}
                            onClick={() => updateFilter({ ...filter, group: null })}
                        >
                            {t("dashboard.All work", { defaultValue: "All work" })} <span className="ml-2 tabular-nums">{first.total}</span>
                        </Button>
                        {MY_WORK_GROUPS.map((group) => (
                            <Button
                                key={group}
                                size="sm"
                                variant={filter.group === group ? "secondary" : "ghost"}
                                className={cn("justify-between gap-2", compact && "w-full")}
                                aria-pressed={filter.group === group}
                                onClick={() => updateFilter({ ...filter, group })}
                            >
                                <span>{t(`dashboard.${MY_WORK_LABELS[group]}`)}</span>
                                <span className="tabular-nums">{first.counts[group]}</span>
                            </Button>
                        ))}
                    </nav>
                    <p className="text-xs text-muted-foreground" aria-live="polite">
                        {t("dashboard.Work shown", {
                            defaultValue: "{{shown}} of {{total}} work items",
                            shown: cards.length,
                            total: filter.group ? first.counts[filter.group] : first.total,
                        })}
                        {query.isFetching ? ` · ${t("dashboard.Refreshing")}` : ""}
                    </p>
                    {!cards.length && <p>{t("dashboard.No work found")}</p>}
                    {(order === "priority" ? [null] : MY_WORK_GROUPS).map((group) => {
                        const grouped = group ? cards.filter((card) => card.group === group) : cards;
                        if (!grouped.length) return null;
                        return (
                            <section key={group ?? "priority"} aria-label={group ? t(`dashboard.${MY_WORK_LABELS[group]}`) : title}>
                                {group && <h2 className="mb-2 text-sm font-semibold">{t(`dashboard.${MY_WORK_LABELS[group]}`)}</h2>}
                                <div className="divide-y rounded-lg border bg-background">
                                    {grouped.map((card) => (
                                        <div key={card.uid} className="min-w-0">
                                            <button
                                                type="button"
                                                className={cn(
                                                    "flex w-full gap-2 px-3 py-2 text-left hover:bg-muted focus-visible:outline-primary",
                                                    compact ? "flex-col items-start" : "flex-col items-start sm:flex-row sm:items-center"
                                                )}
                                                onClick={() => {
                                                    navigate(ROUTES.BOARD.CARD(card.project_uid, card.uid));
                                                    onNavigate?.();
                                                }}
                                            >
                                                <span
                                                    className={cn(
                                                        "min-w-0 font-medium",
                                                        compact ? "w-full break-words text-sm" : "w-full break-words sm:w-auto sm:flex-1 sm:truncate"
                                                    )}
                                                >
                                                    {card.title}
                                                </span>
                                                <span
                                                    className={cn(
                                                        "max-w-full truncate text-xs text-muted-foreground",
                                                        !compact && "hidden max-w-44 sm:inline"
                                                    )}
                                                >
                                                    {card.project_title} · {card.project_column_name}
                                                    {order === "priority" && ` · ${t(`dashboard.${MY_WORK_LABELS[card.group]}`)}`}
                                                </span>
                                                <span className="min-w-0 max-w-full space-y-1 break-words text-xs text-muted-foreground">
                                                    <span className="block">
                                                        {t("card.Deadline")}:{" "}
                                                        {card.deadline_at ? (
                                                            <time dateTime={card.deadline_at} aria-label={t("card.Deadline")}>
                                                                {formatDateTime(new Date(card.deadline_at), i18n.language)}
                                                            </time>
                                                        ) : (
                                                            t("card.No deadline")
                                                        )}
                                                    </span>
                                                    <span className="block">
                                                        {t("card.Updated")}:{" "}
                                                        <time dateTime={card.updated_at} aria-label={t("card.Updated")}>
                                                            <DateDistance date={new Date(card.updated_at)} />
                                                        </time>
                                                    </span>
                                                </span>
                                            </button>
                                            <MyWorkCardActions
                                                key={[actorUID, identity, session, filter.projectUID, filter.group, card.project_uid, card.uid].join(
                                                    ":"
                                                )}
                                                projectUID={card.project_uid}
                                                cardUID={card.uid}
                                            />
                                        </div>
                                    ))}
                                </div>
                            </section>
                        );
                    })}
                    {query.hasNextPage && !query.isError && (
                        <Button size="sm" variant="outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                            {t("dashboard.Load more work", { defaultValue: "Load more work" })}
                        </Button>
                    )}
                </>
            )}
        </section>
    );
}
