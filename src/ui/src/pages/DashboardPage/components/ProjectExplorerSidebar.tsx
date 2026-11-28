import useBoardSplit from "@/core/hooks/useBoardSplit";
import { lazy, useEffect, useMemo, useRef, useState } from "react";
import SharedDndProvider from "@/components/SharedDndProvider";
import { useTranslation } from "react-i18next";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { useAuth } from "@/core/providers/AuthProvider";
import { getUserSettingsStore, useUserSettings } from "@/core/stores/UserSettingsStore";
import { retainProjects, useOpenCards } from "./OpenCardsStore";
import { presentOpenCards } from "./OpenCardsData";
import RecentCardsSection from "./RecentCardsSection";
import ExplorerSection, { EXPLORER_SECTION_LIMIT } from "./ExplorerSection";
import Input from "@/components/base/Input";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import SuspenseComponent from "@/components/base/SuspenseComponent";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import { Project } from "@/core/models";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { buildProjectQuickSwitcherSections, searchProjects } from "@/pages/DashboardPage/components/ProjectDiscovery";

import ProjectWorkloadBadges from "./ProjectWorkloadBadges";
import ProjectUnreadIndicator from "./ProjectUnreadIndicator";
import ProjectAvatar from "./ProjectAvatar";
import ProjectCollectionTree from "./ProjectCollectionTree";
import { BOARD_REFERENCE_DRAG_TYPE } from "./BoardReferenceDrag";
import useProjectCollections from "./useProjectCollections";

const ProjectCollectionDialog = lazy(() => import("./ProjectCollectionDialog"));

const SECTION_IDS = ["cards", "favorites", "recent", "projects"] as const;
type TSectionID = (typeof SECTION_IDS)[number];
interface IExplorerSidebarProps {
    currentProject?: Project.TModel;
    onNavigate?: () => void;
    active?: boolean;
}

export default function ProjectExplorerSidebar(props: IExplorerSidebarProps) {
    return (
        <SharedDndProvider>
            <ExplorerSidebar {...props} />
        </SharedDndProvider>
    );
}

function moveSection(order: TSectionID[], from: TSectionID, to: TSectionID) {
    const next = order.filter((id) => id !== from);
    next.splice(order.indexOf(to), 0, from);
    return next;
}

function ExplorerSidebar({ currentProject, onNavigate, active = true }: IExplorerSidebarProps) {
    const [t] = useTranslation();
    const { currentUser } = useAuth();
    const userUID = currentUser?.uid;
    const openCards = useOpenCards(userUID);
    const collections = useProjectCollections(userUID ?? "");
    const [collectionDialog, setCollectionDialog] = useState(false);
    const wikiHintDismissed = useUserSettings("wiki_hint_dismissed");
    const collapsedByUser = useUserSettings("explorer_open_cards_collapsed");
    const [query, setQuery] = useState("");
    const [searchCollapsed, setSearchCollapsed] = useState(false);
    const savedSectionOrders = useUserSettings("explorer_section_order");
    const storedOrder = userUID ? savedSectionOrders?.[userUID] : undefined;
    const sectionOrder = [...new Set([...(Array.isArray(storedOrder) ? storedOrder : []), ...SECTION_IDS])].filter((id): id is TSectionID =>
        SECTION_IDS.includes(id as TSectionID)
    );
    const [previewOrder, setPreviewOrder] = useState<TSectionID[]>();
    const dragPreview = useRef<{ userUID: string; order: TSectionID[] } | null>(null);
    const savedCollapsedSections = useUserSettings("explorer_sections_collapsed");
    const collapsedSections = userUID ? savedCollapsedSections?.[userUID] : undefined;
    const toggleSection = (id: Exclude<TSectionID, "cards">) => {
        if (!userUID) return;
        const store = getUserSettingsStore();
        const current = store.settings.explorer_sections_collapsed ?? {};
        const sections = current[userUID] ?? {};
        store.updateSettingsByKey("explorer_sections_collapsed", {
            ...current,
            [userUID]: { ...sections, [id]: !sections[id] },
        });
    };
    const [expandedSections, setExpandedSections] = useState<Partial<Record<TSectionID, boolean>>>({});
    const reorderSection = (from: TSectionID, to: TSectionID) => {
        if (!userUID || from === to) return;
        const next = moveSection(sectionOrder, from, to);
        const store = getUserSettingsStore();
        store.updateSettingsByKey("explorer_section_order", { ...store.settings.explorer_section_order, [userUID]: next });
    };
    const previewSection = (from: string, to: string) => {
        if (!userUID || query.trim() || from === to || !SECTION_IDS.includes(from as TSectionID) || !SECTION_IDS.includes(to as TSectionID)) return;
        const order = dragPreview.current?.userUID === userUID ? dragPreview.current.order : sectionOrder;
        const next = moveSection(order, from as TSectionID, to as TSectionID);
        dragPreview.current = { userUID, order: next };
        setPreviewOrder(next);
    };
    const finishSectionDrag = (commit: boolean) => {
        const draft = dragPreview.current;
        dragPreview.current = null;
        setPreviewOrder(undefined);
        if (!commit || !draft || !userUID || draft.userUID !== userUID || query.trim()) return;
        const store = getUserSettingsStore();
        store.updateSettingsByKey("explorer_section_order", { ...store.settings.explorer_section_order, [userUID]: draft.order });
    };
    const sectionHeaderProps = (id: TSectionID): React.ButtonHTMLAttributes<HTMLButtonElement> => ({
        draggable: !!userUID && !query.trim(),
        title: t("dashboard.Reorder section"),
        "aria-keyshortcuts": "Alt+ArrowUp Alt+ArrowDown",
        onKeyDown: (event) => {
            if (!event.altKey || query.trim() || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            const target = sectionOrder[sectionOrder.indexOf(id) + (event.key === "ArrowUp" ? -1 : 1)];
            if (target) reorderSection(id, target);
        },
    });
    const openCardsCollapsed = !!(userUID && collapsedByUser?.[userUID]);
    const navigate = usePageNavigateRef();
    const location = usePageLocation();
    const { data, isPending, isError, isFetching, refetch } = useGetProjects({ refetchOnWindowFocus: true });
    const authorizedProjectUIDs = useMemo(() => new Set(data?.projects.map((project) => project.uid) ?? []), [data]);
    const [showOlderCards, setShowOlderCards] = useState(false);
    useEffect(() => {
        setShowOlderCards(false);
        setCollectionDialog(false);
        setSearchCollapsed(false);
        setExpandedSections({});
        dragPreview.current = null;
        setPreviewOrder(undefined);
    }, [userUID]);
    const visibleOpenCards = presentOpenCards(openCards.filter((card) => authorizedProjectUIDs.has(card.projectUID)));
    const projects = useMemo(() => {
        // A completed dashboard response is authoritative for Explorer references.
        if (!data && isPending && currentProject) return [currentProject];
        return data?.projects ?? [];
    }, [currentProject, data, isPending]);
    useEffect(() => {
        if (!data || !userUID) return;
        if (openCards.some((card) => !authorizedProjectUIDs.has(card.projectUID))) {
            retainProjects(userUID, authorizedProjectUIDs);
        }
    }, [data, openCards, authorizedProjectUIDs, userUID]);
    const projectsByUID = useMemo(() => new Map(data?.projects.map((project) => [project.uid, project])), [data]);
    const [favoriteVersion, setFavoriteVersion] = useState(0);
    useEffect(() => {
        const unsubscribers = projects.map((project) => project.subscribeFields(["starred"], () => setFavoriteVersion((version) => version + 1)));
        return () => unsubscribers.forEach((unsubscribe) => unsubscribe());
    }, [projects]);
    const sections = useMemo(() => buildProjectQuickSwitcherSections(projects), [projects, favoriteVersion]);
    const favoriteCollections = collections.collections.filter((item) => item.starred);
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
            <div
                {...{ elementtiming: "workbench-explorer-title" }}
                className="shrink-0 border-b px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
                {t("common.Explorer")}
            </div>
            <div className="shrink-0 p-2">
                <Input
                    value={query}
                    onChange={(event) => {
                        setQuery(event.currentTarget.value);
                        setSearchCollapsed(false);
                        finishSectionDrag(false);
                    }}
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
                {(query.trim() ? ["projects" as const] : (previewOrder ?? sectionOrder)).map((id, index) => {
                    if (id === "cards")
                        return !query.trim() && userUID ? (
                            <RecentCardsSection
                                key={id}
                                headerProps={sectionHeaderProps("cards")}
                                reorder={{ id, index, preview: previewSection, finish: finishSectionDrag }}
                                active={active}
                                userUID={userUID}
                                cards={visibleOpenCards}
                                projectsByUID={projectsByUID}
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
                    if (!group || (!group.projects.length && id !== "projects" && !(id === "favorites" && favoriteCollections.length))) return null;
                    const expanded = !!expandedSections[id];
                    const shownCollections =
                        id === "favorites" ? (expanded ? favoriteCollections : favoriteCollections.slice(0, EXPLORER_SECTION_LIMIT)) : [];
                    const displayed = expanded
                        ? group.projects
                        : group.projects.slice(0, Math.max(0, EXPLORER_SECTION_LIMIT - shownCollections.length));
                    return (
                        <ExplorerSection
                            key={id}
                            title={group.title}
                            icon={id === "favorites" ? "star" : undefined}
                            count={
                                group.projects.length +
                                (id === "projects" ? collections.collections.length : id === "favorites" ? favoriteCollections.length : 0)
                            }
                            collapsed={query.trim() ? searchCollapsed : !!collapsedSections?.[id]}
                            expanded={expanded}
                            onToggle={() => (query.trim() ? setSearchCollapsed((collapsed) => !collapsed) : toggleSection(id))}
                            onExpand={() => setExpandedSections((state) => ({ ...state, [id]: !state[id] }))}
                            headerProps={query.trim() ? undefined : sectionHeaderProps(id)}
                            reorder={query.trim() ? undefined : { id, index, preview: previewSection, finish: finishSectionDrag }}
                            actions={
                                id === "projects" && userUID ? (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="size-6"
                                        aria-label={t("dashboard.Add project collection")}
                                        disabled={collections.busy || collections.error}
                                        onClick={() => setCollectionDialog(true)}
                                    >
                                        <IconComponent icon="plus" size="3" />
                                    </Button>
                                ) : undefined
                            }
                        >
                            {id === "favorites" && shownCollections.length > 0 && (
                                <ProjectCollectionTree
                                    collections={collections.collections}
                                    visibleCollectionUIDs={shownCollections.map((item) => item.uid)}
                                    projects={projects}
                                    busy={collections.busy}
                                    save={collections.save}
                                    navigate={(uid) => {
                                        navigate(ROUTES.BOARD.MAIN(uid));
                                        onNavigate?.();
                                    }}
                                />
                            )}
                            {id === "projects" && !query.trim() && (
                                <>
                                    {collections.error && (
                                        <Button type="button" variant="ghost" size="sm" onClick={() => void collections.retry()}>
                                            {t("dashboard.Reload project collections")}
                                        </Button>
                                    )}
                                    {collections.collections.length > 0 && (
                                        <ProjectCollectionTree
                                            collections={collections.collections}
                                            projects={projects}
                                            busy={collections.busy}
                                            save={collections.save}
                                            navigate={(uid) => {
                                                navigate(ROUTES.BOARD.MAIN(uid));
                                                onNavigate?.();
                                            }}
                                        />
                                    )}
                                </>
                            )}
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
            {collectionDialog && userUID && (
                <SuspenseComponent isPage>
                    <ProjectCollectionDialog
                        key={userUID}
                        opened
                        onOpenChange={setCollectionDialog}
                        boards={projects.map((project) => ({ uid: project.uid, title: project.title }))}
                        collections={collections.collections}
                        save={collections.save}
                        refreshBoards={() => void refetch()}
                    />
                </SuspenseComponent>
            )}
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
    const [t] = useTranslation();
    const split = useBoardSplit();
    const title = project.useField("title");
    const starred = project.useField("starred");
    return (
        <div className="group flex min-w-0 items-center gap-1" style={{ containerType: "inline-size", containerName: "project-workload" }}>
            <button
                type="button"
                onClick={onClick}
                draggable
                onDragStart={(event) => {
                    event.stopPropagation();
                    event.dataTransfer.setData(BOARD_REFERENCE_DRAG_TYPE, project.uid);
                    event.dataTransfer.effectAllowed = "copy";
                }}
                aria-current={active ? "page" : undefined}
                className={cn(
                    "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                    "aria-[current=page]:bg-muted aria-[current=page]:text-primary"
                )}
            >
                <ProjectAvatar project={project} className="size-6 rounded-md text-xs" />
                <span className="truncate">{title}</span>
                {starred && <IconComponent icon="star" size="3" className="shrink-0 text-muted-foreground" aria-hidden="true" />}
                <ProjectUnreadIndicator project={project} className="static ml-auto shrink-0" />
            </button>
            {split && (
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="hidden size-6 opacity-0 focus:opacity-100 group-hover:opacity-100 md:inline-flex"
                    aria-label={t("common.Open {{title}} in split view", { title })}
                    disabled={split.panes.length >= 2}
                    onClick={() => split.open(project.uid)}
                >
                    <IconComponent icon="columns-2" size="3" />
                </Button>
            )}
            <ProjectWorkloadBadges projectUID={project.uid} compact onNavigate={onNavigate} />
        </div>
    );
}
