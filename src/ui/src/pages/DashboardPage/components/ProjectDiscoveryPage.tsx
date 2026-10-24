import { memo, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import Box from "@/components/base/Box";
import Input from "@/components/base/Input";
import Skeleton from "@/components/base/Skeleton";
import type { IGetProjectsResponse } from "@/controllers/api/dashboard/useGetProjects";
import { SkeletonProjectList } from "@/pages/DashboardPage/components/ProjectList";
import { Project, ProjectColumn } from "@/core/models";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import { workloadTotal } from "./ProjectWorkload";
import ProjectCompactList from "@/pages/DashboardPage/components/ProjectCompactList";
import {
    buildProjectDiscoverySections,
    parseProjectListView,
    projectListViewStorageKey,
    selectDiscoveryProjects,
    buildProjectSearchSections,
    type TProjectDiscoveryFilter,
    type TProjectDiscoverySort,
    PROJECT_QUICK_SWITCHER_EVENT,
    type TProjectListView,
} from "@/pages/DashboardPage/components/ProjectDiscovery";

export function SkeletonProjectDiscoveryPage() {
    return (
        <>
            <Box px="2" mt="3">
                <Skeleton h="9" />
            </Box>
            <Flex justify="end" gap="1" px="2" mt="2">
                <Skeleton className="size-8" />
                <Skeleton className="size-8" />
            </Flex>
            <Box mt="2">
                <SkeletonProjectList />
            </Box>
        </>
    );
}

interface IProjectDiscoveryPageProps {
    userUID: string;
    projectsData?: IGetProjectsResponse;
    isProjectsFetching: bool;
    isProjectsLoading: bool;
    hasProjectsError?: bool;
    onRetryProjects?: () => Promise<unknown>;
    updateStarredProjects: React.DispatchWithoutAction;
    scrollAreaUpdater: [number, React.DispatchWithoutAction];
}

function ProjectDiscoveryObserver({ project, onChange }: { project: Project.TModel; onChange: () => void }) {
    const title = project.useField("title");
    const starred = project.useField("starred");
    const lastActivityAt = project.useField("last_activity_at");
    const viewed = project.useField("last_viewed_at");
    const related = project.useField("related_to_current_user");
    const unread = project.useField("board_has_unread_change");
    useEffect(onChange, [title, starred, lastActivityAt, viewed, related, unread, onChange]);
    return null;
}

const filterKeys = ["unread", "related", "favorites", "unfinished"] as const;
const sortKeys = ["default", "activity", "viewed", "name", "unread"] as const;
const filterLabels = { unread: "Unread changes", related: "Related to me", favorites: "Favorites", unfinished: "Unfinished work" };
const sortLabels = { default: "Default order", activity: "Recent activity", viewed: "Recently viewed", name: "Name", unread: "Unread first" };

function readView(userUID: string): TProjectListView {
    try {
        return parseProjectListView(localStorage.getItem(projectListViewStorageKey(userUID)));
    } catch {
        return "compact";
    }
}

function ProjectDiscoverySession({
    userUID,
    projectsData,
    isProjectsFetching,
    isProjectsLoading,
    hasProjectsError = false,
    onRetryProjects,
    updateStarredProjects: updateHeaderStarredProjects,
    scrollAreaUpdater,
}: IProjectDiscoveryPageProps): React.JSX.Element {
    const [t] = useTranslation();
    const [revision, refresh] = useReducer((value) => value + 1, 0);
    const onChange = useCallback(() => refresh(), []);
    const [searchQuery, setSearchQuery] = useState("");
    const searchRef = useRef<HTMLInputElement>(null);
    const [view, setView] = useState<TProjectListView>(() => readView(userUID));
    const [filters, setFilters] = useState<TProjectDiscoveryFilter[]>([]);
    const [sort, setSort] = useState<TProjectDiscoverySort>("default");
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const projectUIDs = useMemo(() => [...new Set((projectsData?.projects ?? []).map((project) => project.uid))], [projectsData]);
    const authorizedUIDs = useMemo(() => new Set(projectUIDs), [projectUIDs]);
    const loaded = Project.Model.useModels((model) => authorizedUIDs.has(model.uid), [authorizedUIDs, revision]);
    const columns = ProjectColumn.Model.useModels((column) => authorizedUIDs.has(column.project_uid), [authorizedUIDs, revision]);
    const projects = useMemo(() => {
        const byUID = new Map(loaded.map((project) => [project.uid, project]));
        return projectUIDs.map((uid) => byUID.get(uid)).filter((project): project is Project.TModel => !!project);
    }, [loaded, projectUIDs]);
    const unfinishedUIDs = useMemo(() => {
        const byProject = new Map<string, ProjectColumn.TModel[]>();
        for (const column of columns) {
            const group = byProject.get(column.project_uid) ?? [];
            group.push(column);
            byProject.set(column.project_uid, group);
        }
        return new Set([...byProject].filter(([, group]) => (workloadTotal(group) ?? 0) > 0).map(([uid]) => uid));
    }, [columns, revision]);
    const results = useMemo(
        () => selectDiscoveryProjects(projects, searchQuery, filters, sort, unfinishedUIDs),
        [projects, searchQuery, filters, sort, unfinishedUIDs, revision]
    );
    const searching = !!searchQuery.trim();
    const sections = useMemo(
        () =>
            searching
                ? { ...buildProjectSearchSections(results), recent: [] }
                : { ...buildProjectDiscoverySections(results, undefined, true), other: [] },
        [results, searching]
    );
    const updateStars = () => {
        updateHeaderStarredProjects();
        refresh();
    };
    const changeView = (next: TProjectListView) => {
        setView(next);
        try {
            localStorage.setItem(projectListViewStorageKey(userUID), next);
        } catch {
            /* Storage can be unavailable in private sessions. */
        }
    };
    const list = (key: string, title: string, items: Project.TModel[], initialVisibleCount?: number) => (
        <ProjectCompactList
            key={key}
            title={t(`dashboard.${title}`)}
            projects={items}
            view={view}
            updateStarredProjects={updateStars}
            initialVisibleCount={initialVisibleCount}
            expanded={expanded[key] ?? false}
            onExpandedChange={(value) => setExpanded((previous) => ({ ...previous, [key]: value }))}
            showRelatedBadge={searching}
            listClassName={key === "favorites" || key === "recent" ? "xl:grid-cols-2" : undefined}
            scrollAreaUpdater={scrollAreaUpdater}
        />
    );
    return (
        <>
            {projects.map((project) => (
                <ProjectDiscoveryObserver key={project.uid} project={project} onChange={onChange} />
            ))}
            {columns.map((column) => (
                <DiscoveryColumnObserver key={column.uid} column={column} onChange={onChange} />
            ))}
            <Flex items="center" gap="2" px="2" mt="3">
                <Input
                    ref={searchRef}
                    wrapperProps={{ className: "min-w-0 flex-1" }}
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.currentTarget.value)}
                    placeholder={t("dashboard.Search projects...")}
                    aria-label={t("dashboard.Search projects")}
                    leftIcon={<Search />}
                    rightIcon={
                        searchQuery ? (
                            <Button
                                type="button"
                                size="icon-sm"
                                variant="ghost"
                                aria-label={t("dashboard.Clear search")}
                                onClick={() => {
                                    setSearchQuery("");
                                    searchRef.current?.focus();
                                }}
                            >
                                <IconComponent icon="x" size="4" />
                            </Button>
                        ) : undefined
                    }
                />
                <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    className="shrink-0 md:hidden"
                    aria-label={t("dashboard.Quick switcher")}
                    onClick={() => window.dispatchEvent(new Event(PROJECT_QUICK_SWITCHER_EVENT))}
                >
                    <IconComponent icon="search" size="4" />
                </Button>
            </Flex>
            <Flex
                className="min-w-0 flex-nowrap overflow-x-auto"
                items="center"
                gap="2"
                px="2"
                mt="2"
                role="toolbar"
                aria-label={t("dashboard.Project filters") as string}
            >
                {filterKeys.map((filter) => (
                    <Button
                        key={filter}
                        type="button"
                        size="sm"
                        className="shrink-0"
                        variant={filters.includes(filter) ? "secondary" : "outline"}
                        aria-pressed={filters.includes(filter)}
                        onClick={() =>
                            setFilters((current) => (current.includes(filter) ? current.filter((value) => value !== filter) : [...current, filter]))
                        }
                    >
                        {t(`dashboard.${filterLabels[filter]}`)}
                        {filters.includes(filter) && <IconComponent icon="x" size="3" />}
                    </Button>
                ))}
                <label className="flex shrink-0 items-center gap-2 text-sm">
                    {t("dashboard.Sort projects")}
                    <select
                        className="h-8 max-w-full rounded-md border bg-background px-2 text-sm"
                        aria-label={t("dashboard.Sort projects")}
                        value={sort}
                        onChange={(event) => setSort(event.currentTarget.value as TProjectDiscoverySort)}
                    >
                        {sortKeys.map((key) => (
                            <option key={key} value={key}>
                                {t(`dashboard.${sortLabels[key]}`)}
                            </option>
                        ))}
                    </select>
                </label>
                {filters.length > 0 && (
                    <Button type="button" variant="ghost" size="sm" className="shrink-0" onClick={() => setFilters([])}>
                        {t("dashboard.Reset filters")}
                    </Button>
                )}
                <Flex role="group" className="ml-auto shrink-0" gap="1" aria-label={t("dashboard.Project view") as string}>
                    {(["compact", "cards"] as const).map((mode) => (
                        <Button
                            key={mode}
                            type="button"
                            size="icon-sm"
                            variant={view === mode ? "secondary" : "ghost"}
                            aria-label={t(mode === "compact" ? "dashboard.Compact view" : "dashboard.Card view")}
                            aria-pressed={view === mode}
                            onClick={() => changeView(mode)}
                        >
                            <IconComponent icon={mode === "compact" ? "list" : "layout-grid"} size="4" />
                        </Button>
                    ))}
                </Flex>
            </Flex>
            {hasProjectsError && (
                <Flex role="alert" items="center" justify="center" gap="2" px="2" py="3">
                    <span className="text-sm text-muted-foreground">{t("dashboard.Could not load projects")}</span>
                    <Button type="button" size="sm" variant="outline" disabled={isProjectsFetching} onClick={() => void onRetryProjects?.()}>
                        {t("dashboard.Retry")}
                    </Button>
                </Flex>
            )}
            <Box data-project-discovery-mode={searching ? "search" : "default"}>
                {(isProjectsLoading || isProjectsFetching) && results.length === 0 ? (
                    <SkeletonProjectList />
                ) : (
                    <>
                        {searching ? (
                            <>
                                <h2 className="mt-4 px-2 text-sm font-semibold" role="status">
                                    {t("dashboard.Search results")} · {results.length}
                                </h2>
                                {list("search-favorites", "Favorites", sections.favorites)}
                                {list("search-related", "Related to me", sections.related)}
                                {list("search-other", "Other projects", sections.other)}
                            </>
                        ) : (
                            <>
                                <div className="grid min-w-0 gap-x-4 md:grid-cols-2">
                                    {list("favorites", "Favorites", sections.favorites, 4)}
                                    {list("recent", "Recent work", sections.recent, 4)}
                                </div>
                                {list("related", "Related to me", sections.related, 4)}
                                {list("all", "All projects", results)}
                            </>
                        )}
                        {results.length === 0 && !hasProjectsError && (
                            <h2 className="py-3 text-center text-lg text-accent-foreground">{t("dashboard.No projects found")}</h2>
                        )}
                    </>
                )}
            </Box>
        </>
    );
}

function DiscoveryColumnObserver({ column, onChange }: { column: ProjectColumn.TModel; onChange: () => void }) {
    const count = column.useField("incomplete_count");
    const archived = column.useField("is_archive");
    const stage = column.useField("workflow_stage");
    const completed = column.useField("workflow_counts_as_completed");
    useEffect(onChange, [count, archived, stage, completed, onChange]);
    return null;
}

const ProjectDiscoveryPage = memo((props: IProjectDiscoveryPageProps) => <ProjectDiscoverySession key={props.userUID} {...props} />);
export default ProjectDiscoveryPage;
