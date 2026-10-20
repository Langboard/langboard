import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import { useAuth } from "@/core/providers/AuthProvider";
import { getUserSettingsStore, useUserSettings } from "@/core/stores/UserSettingsStore";
import { retainProjects, useOpenCards } from "./OpenCardsStore";
import { recentOpenCards } from "./OpenCardsData";
import RecentCardsSection from "./RecentCardsSection";
import ExplorerSection, { EXPLORER_SECTION_LIMIT } from "./ExplorerSection";
import Input from "@/components/base/Input";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import { Project } from "@/core/models";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { buildProjectQuickSwitcherSections, searchProjects } from "@/pages/DashboardPage/components/ProjectDiscovery";

import ProjectWorkloadBadges from "./ProjectWorkloadBadges";

const SECTION_IDS = ["cards", "favorites", "recent", "projects"] as const;
type TSectionID = (typeof SECTION_IDS)[number];
const SECTION_DRAG_TYPE = "application/x-langboard-explorer-section";

export default function ProjectExplorerSidebar({
    currentProject,
    onNavigate,
    active = true,
}: {
    currentProject?: Project.TModel;
    onNavigate?: () => void;
    active?: boolean;
}) {
    const [t] = useTranslation();
    const { currentUser } = useAuth();
    const userUID = currentUser?.uid;
    const openCards = useOpenCards(userUID);
    const wikiHintDismissed = useUserSettings("wiki_hint_dismissed");
    const collapsedByUser = useUserSettings("explorer_open_cards_collapsed");
    const [query, setQuery] = useState("");
    const savedSectionOrders = useUserSettings("explorer_section_order");
    const storedOrder = userUID ? savedSectionOrders?.[userUID] : undefined;
    const sectionOrder = [...new Set([...(Array.isArray(storedOrder) ? storedOrder : []), ...SECTION_IDS])].filter((id): id is TSectionID =>
        SECTION_IDS.includes(id as TSectionID)
    );
    const [collapsedSections, setCollapsedSections] = useState<Partial<Record<TSectionID, boolean>>>({});
    const [expandedSections, setExpandedSections] = useState<Partial<Record<TSectionID, boolean>>>({});
    const reorderSection = (from: TSectionID, to: TSectionID) => {
        if (!userUID || from === to) return;
        const next = sectionOrder.filter((id) => id !== from);
        next.splice(sectionOrder.indexOf(to), 0, from);
        const store = getUserSettingsStore();
        store.updateSettingsByKey("explorer_section_order", { ...store.settings.explorer_section_order, [userUID]: next });
    };
    const sectionHeaderProps = (id: TSectionID): React.ButtonHTMLAttributes<HTMLButtonElement> => ({
        draggable: !!userUID && !query.trim(),
        title: t("dashboard.Reorder section"),
        "aria-keyshortcuts": "Alt+ArrowUp Alt+ArrowDown",
        onDragStart: (event) => {
            event.stopPropagation();
            event.dataTransfer.setData(SECTION_DRAG_TYPE, id);
            event.dataTransfer.effectAllowed = "move";
        },
        onDragOver: (event) => {
            if (!event.dataTransfer.types.includes(SECTION_DRAG_TYPE)) return;
            event.preventDefault();
            event.stopPropagation();
            event.dataTransfer.dropEffect = "move";
        },
        onDrop: (event) => {
            const from = event.dataTransfer.getData(SECTION_DRAG_TYPE);
            if (!SECTION_IDS.includes(from as TSectionID)) return;
            event.preventDefault();
            event.stopPropagation();
            reorderSection(from as TSectionID, id);
        },
        onKeyDown: (event) => {
            if (!event.altKey || query.trim() || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            const target = sectionOrder[sectionOrder.indexOf(id) + (event.key === "ArrowUp" ? -1 : 1)];
            if (target) reorderSection(id, target);
        },
    });
    const openCardsCollapsed = !!(userUID && collapsedByUser?.[userUID]);
    const navigate = usePageNavigateRef();
    const location = useLocation();
    const { data, isPending, isError, isFetching, refetch } = useGetProjects({ refetchOnWindowFocus: true });
    const authorizedProjectUIDs = useMemo(() => new Set(data?.projects.map((project) => project.uid) ?? []), [data]);
    const [showOlderCards, setShowOlderCards] = useState(false);
    useEffect(() => {
        setShowOlderCards(false);
        setCollapsedSections({});
        setExpandedSections({});
    }, [userUID]);
    const visibleOpenCards = recentOpenCards(openCards.filter((card) => authorizedProjectUIDs.has(card.projectUID)));
    const projects = useMemo(() => {
        const all = data?.projects ?? [];
        return currentProject && !all.some((project) => project.uid === currentProject.uid) ? [currentProject, ...all] : all;
    }, [currentProject, data]);
    useEffect(() => {
        if (!data || !userUID) return;
        if (openCards.some((card) => !authorizedProjectUIDs.has(card.projectUID))) {
            retainProjects(userUID, authorizedProjectUIDs);
        }
    }, [data, openCards, authorizedProjectUIDs, userUID]);
    const sections = useMemo(() => buildProjectQuickSwitcherSections(projects), [projects]);
    const currentProjectUID = location.pathname.startsWith("/board/") ? location.pathname.split("/")[2] : undefined;
    const groups = query.trim()
        ? [{ id: "projects" as const, title: t("dashboard.Projects"), projects: searchProjects(projects, query) }]
        : [
              { id: "favorites" as const, title: t("dashboard.Favorites"), projects: sections.favorites },
              { id: "recent" as const, title: t("dashboard.Recent work"), projects: sections.recent },
              { id: "projects" as const, title: t("dashboard.Projects"), projects: [...sections.related, ...sections.other] },
          ];

    return (
        <nav aria-label={t("common.Explorer")} data-workbench-explorer="" className="flex size-full min-w-0 flex-col overflow-hidden">
            <div className="shrink-0 border-b px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("common.Explorer")}
            </div>
            <div className="shrink-0 p-2">
                <Input
                    value={query}
                    onChange={(event) => setQuery(event.currentTarget.value)}
                    placeholder={t("dashboard.Search projects...")}
                    aria-label={t("dashboard.Search projects")}
                    leftIcon={<IconComponent icon="search" />}
                    clearable
                />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" data-explorer-section-list="">
                {currentProject && !query.trim() && (
                    <section className="mb-3 shrink-0" aria-label={currentProject.title}>
                        <h2 className="px-2 py-1 text-xs font-medium text-muted-foreground">{currentProject.title}</h2>
                        {userUID && !wikiHintDismissed?.[userUID] && (
                            <div className="mb-1 flex items-start gap-1 rounded-md bg-muted/50 px-2 py-1.5 text-xs text-muted-foreground">
                                <span className="flex-1">{t("dashboard.Project documents are in Wiki")}</span>
                                <button
                                    type="button"
                                    aria-label={t("common.Close")}
                                    onClick={() =>
                                        getUserSettingsStore().updateSettingsByKey("wiki_hint_dismissed", {
                                            ...wikiHintDismissed,
                                            [userUID]: true,
                                        })
                                    }
                                    className="rounded p-0.5 hover:bg-accent"
                                >
                                    <IconComponent icon="x" size="3" />
                                </button>
                            </div>
                        )}
                        {(
                            [
                                { name: t("board.Board"), icon: "columns-3", route: ROUTES.BOARD.MAIN(currentProject.uid) },
                                { name: t("board.Wiki"), icon: "notebook-pen", route: ROUTES.BOARD.WIKI(currentProject.uid) },
                            ] as const
                        ).map((item) => (
                            <button
                                key={item.route}
                                type="button"
                                aria-current={
                                    location.pathname === item.route ||
                                    (item.icon === "notebook-pen" && location.pathname.startsWith(`${item.route}/`))
                                        ? "page"
                                        : undefined
                                }
                                className={cn(
                                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                                    "aria-[current=page]:bg-muted aria-[current=page]:text-primary"
                                )}
                                onClick={() => {
                                    navigate(item.route);
                                    onNavigate?.();
                                }}
                            >
                                <IconComponent icon={item.icon} size="4" />
                                <span>{item.name}</span>
                            </button>
                        ))}
                    </section>
                )}
                {isPending && (
                    <p role="status" className="px-2 py-3 text-xs text-muted-foreground">
                        {t("common.Loading...")}
                    </p>
                )}
                {isError && (
                    <div role="alert" className="flex flex-wrap items-center gap-2 px-2 py-3">
                        <span className="text-xs text-muted-foreground">{t("dashboard.Could not load projects")}</span>
                        <Button type="button" size="sm" variant="outline" disabled={isFetching} onClick={() => void refetch()}>
                            {t("dashboard.Retry")}
                        </Button>
                    </div>
                )}
                {(query.trim() ? ["projects" as const] : sectionOrder).map((id) => {
                    if (id === "cards")
                        return !query.trim() && userUID ? (
                            <RecentCardsSection
                                key={id}
                                headerProps={sectionHeaderProps("cards")}
                                active={active}
                                userUID={userUID}
                                cards={visibleOpenCards}
                                collapsed={openCardsCollapsed}
                                expanded={showOlderCards}
                                onExpand={() => setShowOlderCards((shown) => !shown)}
                                onToggle={() =>
                                    getUserSettingsStore().updateSettingsByKey("explorer_open_cards_collapsed", {
                                        ...collapsedByUser,
                                        [userUID]: !openCardsCollapsed,
                                    })
                                }
                                onNavigate={onNavigate}
                            />
                        ) : null;
                    const group = groups.find((group) => group.id === id);
                    if (!group?.projects.length) return null;
                    const expanded = !!expandedSections[id];
                    const displayed = expanded ? group.projects : group.projects.slice(0, EXPLORER_SECTION_LIMIT);
                    return (
                        <ExplorerSection
                            key={id}
                            title={group.title}
                            count={group.projects.length}
                            collapsed={!!collapsedSections[id]}
                            expanded={expanded}
                            onToggle={() => setCollapsedSections((state) => ({ ...state, [id]: !state[id] }))}
                            onExpand={() => setExpandedSections((state) => ({ ...state, [id]: !state[id] }))}
                            headerProps={query.trim() ? undefined : sectionHeaderProps(id)}
                        >
                            {displayed.map((project) => (
                                <ProjectExplorerItem
                                    key={project.uid}
                                    project={project}
                                    active={currentProjectUID === project.uid}
                                    onNavigate={onNavigate}
                                    onClick={() => {
                                        navigate(ROUTES.BOARD.MAIN(project.uid), { state: { commandPaletteFocus: true } });
                                        onNavigate?.();
                                    }}
                                />
                            ))}
                        </ExplorerSection>
                    );
                })}
            </div>
        </nav>
    );
}

function ProjectExplorerItem({
    project,
    active,
    onClick,
    onNavigate,
}: {
    project: Project.TModel;
    active: boolean;
    onClick: () => void;
    onNavigate?: () => void;
}) {
    const title = project.useField("title");
    const starred = project.useField("starred");
    return (
        <div className="flex min-w-0 items-center gap-1" style={{ containerType: "inline-size", containerName: "project-workload" }}>
            <button
                type="button"
                onClick={onClick}
                aria-current={active ? "page" : undefined}
                className={cn(
                    "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                    "aria-[current=page]:bg-muted aria-[current=page]:text-primary"
                )}
            >
                <IconComponent icon={starred ? "star" : "folder-kanban"} size="4" />
                <span className="truncate">{title}</span>
            </button>
            <ProjectWorkloadBadges projectUID={project.uid} compact onNavigate={onNavigate} />
        </div>
    );
}
