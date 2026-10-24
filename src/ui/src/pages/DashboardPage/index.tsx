import PersonalSignalInbox from "./PersonalSignalInbox";
import useAuthStore from "@/core/stores/AuthStore";
import usePinnedAppRail from "@/core/apps/usePinnedAppRail";
import { memo, useCallback, useMemo, useReducer, useState } from "react";
import { IHeaderNavItem } from "@/components/Header/types";
import { DashboardStyledLayout } from "@/components/Layout";
import { IActivityRailItem } from "@/components/Layout/ActivityRail";
import useGetAllStarredProjects from "@/controllers/api/dashboard/useGetAllStarredProjects";
import { ROUTES } from "@/core/routing/constants";
import ProjectPage from "@/pages/DashboardPage/ProjectPage";
import CardsPage, { SkeletonCardsPage } from "@/pages/DashboardPage/CardsPage";
import TrackingPage, { SkeletonTrackingPage } from "@/pages/DashboardPage/TrackingPage";
import MyWorkPage from "@/pages/DashboardPage/MyWorkPage";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { Navigate } from "react-router";
import { DashboardProvider } from "@/core/providers/DashboardProvider";
import { useAuth } from "@/core/providers/AuthProvider";
import { Project } from "@/core/models";
import { useTranslation } from "react-i18next";
import { SkeletonProjectDiscoveryPage } from "@/pages/DashboardPage/components/ProjectDiscoveryPage";
import ProjectExplorerSidebar from "@/pages/DashboardPage/components/ProjectExplorerSidebar";
import { WORKBENCH_OPEN_MY_WORK_EVENT, WORKBENCH_TOGGLE_CONTEXT_EVENT } from "@/pages/DashboardPage/components/WorkbenchCommands";
import { useEffect } from "react";
import { useWorkbenchContextOpen, useUserSettings } from "@/core/stores/UserSettingsStore";
import useResizeEvent from "@/core/hooks/useResizeEvent";
import { ScreenMap } from "@/core/utils/VariantUtils";

const DashboardProxy = memo((): React.JSX.Element => {
    const [t] = useTranslation();
    const { currentUser } = useAuth();
    const identityVersion = useAuthStore((state) => state.getIdentityVersion());
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const actorScope = `${currentUser?.uid}:${identityVersion}:${sessionVersion}`;
    const [isExplorerOpen, setIsExplorerOpen] = useWorkbenchContextOpen(currentUser?.uid);
    const collapsedPanels = useUserSettings("workbench_sidebar_collapsed");
    const desktopContextVisible = isExplorerOpen && !collapsedPanels?.[currentUser?.uid ?? ""];
    const [isMobile, setIsMobile] = useState(window.innerWidth < ScreenMap.size.md);
    const [isMobileExplorerOpen, setIsMobileExplorerOpen] = useState(false);
    const [contextMode, setContextMode] = useState<"explorer" | "my-work">("explorer");
    const showContext = useCallback(
        (mode: "explorer" | "my-work") => {
            setContextMode(mode);
            if (isMobile) setIsMobileExplorerOpen(true);
            else setIsExplorerOpen(true);
        },
        [isMobile, setIsExplorerOpen]
    );
    const toggleContext = (mode: "explorer" | "my-work") => {
        if (contextMode === mode && (isMobile ? isMobileExplorerOpen : desktopContextVisible)) {
            if (isMobile) setIsMobileExplorerOpen(false);
            else setIsExplorerOpen(false);
        } else showContext(mode);
    };
    useResizeEvent({ doneCallback: () => setIsMobile(window.innerWidth < ScreenMap.size.md) }, []);
    const toggleExplorer = useCallback(() => {
        if (isMobile) setIsMobileExplorerOpen((open) => !open);
        else setIsExplorerOpen((open) => !open);
    }, [isMobile, setIsExplorerOpen]);
    useEffect(() => {
        const toggle = toggleExplorer;
        const openMyWork = () => showContext("my-work");
        window.addEventListener(WORKBENCH_TOGGLE_CONTEXT_EVENT, toggle);
        window.addEventListener(WORKBENCH_OPEN_MY_WORK_EVENT, openMyWork);
        return () => {
            window.removeEventListener(WORKBENCH_TOGGLE_CONTEXT_EVENT, toggle);
            window.removeEventListener(WORKBENCH_OPEN_MY_WORK_EVENT, openMyWork);
        };
    }, [toggleExplorer, showContext]);
    const navigate = usePageNavigateRef();
    const pinnedApps = usePinnedAppRail({ userUID: currentUser?.uid });
    const [pageType, tabName] = location.pathname.split("/").slice(2);
    const { data, isFetching } = useGetAllStarredProjects();
    const scrollAreaUpdater = useReducer((x) => x + 1, 0);
    const [updatedStarredProjects, updateStarredProjects] = useReducer((x) => x + 1, 0);
    const [scrollAreaMutable] = scrollAreaUpdater;
    const starredProjects = Project.Model.useModels((model) => model.starred, [data, isFetching, updatedStarredProjects]);

    const headerNavs = useMemo<IHeaderNavItem[]>(() => {
        const navs: Record<string, IHeaderNavItem> = {
            projects: {
                name: t("dashboard.Projects"),
                onClick: () => {
                    navigate(ROUTES.DASHBOARD.PROJECTS.ALL, { smooth: true });
                },
            },
            cards: {
                name: t("dashboard.Cards"),
                onClick: () => {
                    navigate(ROUTES.DASHBOARD.CARDS, { smooth: true });
                },
            },
            starred: {
                name: t("dashboard.Starred"),
                subNavs: starredProjects.map((project) => ({
                    name: project.title,
                    onClick: () => {
                        navigate(ROUTES.BOARD.MAIN(project.uid));
                    },
                })),
            },
            tracking: {
                name: t("dashboard.Tracking"),
                onClick: () => {
                    navigate(ROUTES.DASHBOARD.TRACKING, { smooth: true });
                },
            },
        };

        switch (pageType) {
            case "cards":
                navs.cards.active = true;
                break;
            case "tracking":
                navs.tracking.active = true;
                break;
            case "projects":
                navs.projects.active = true;
                break;
        }

        return Object.values(navs);
    }, [pageType, starredProjects]);

    let pageContent;
    let skeletonContent;
    switch (pageType) {
        case "cards":
            pageContent = <CardsPage />;
            skeletonContent = <SkeletonCardsPage />;
            break;
        case "tracking":
            pageContent = <TrackingPage />;
            skeletonContent = <SkeletonTrackingPage />;
            break;
        case "inbox":
            pageContent = <PersonalSignalInbox key={actorScope} actorScope={actorScope} />;
            skeletonContent = <SkeletonCardsPage />;
            break;
        case "my-work":
            pageContent = <MyWorkPage />;
            skeletonContent = <SkeletonCardsPage />;
            break;
        case "projects":
            switch (tabName) {
                case "all":
                case "starred":
                case "recent":
                case "unstarred":
                    pageContent = <ProjectPage updateStarredProjects={updateStarredProjects} scrollAreaUpdater={scrollAreaUpdater} />;
                    skeletonContent = <SkeletonProjectDiscoveryPage />;
                    break;
                default:
                    return <Navigate to={ROUTES.DASHBOARD.PROJECTS.ALL} />;
            }
            break;
        default:
            return <Navigate to={ROUTES.DASHBOARD.PROJECTS.ALL} />;
    }

    // Dashboard navigation owns its routes and panels; board-scoped actions stay in BoardProxy.
    const dashboardRailItems: IActivityRailItem[] = [
        {
            icon: "house",
            label: t("dashboard.Projects"),
            mobile: "primary",
            onClick: headerNavs[0].onClick!,
            active: pageType === "projects" && tabName === "all",
        },
        {
            icon: "panel-left",
            label: t("common.Explorer"),
            onClick: () => toggleContext("explorer"),
            active: contextMode === "explorer" && (isMobile ? isMobileExplorerOpen : desktopContextVisible),
        },
        { icon: "layout-dashboard", label: t("dashboard.Cards"), onClick: headerNavs[1].onClick!, active: pageType === "cards" },
        {
            icon: "list-checks",
            label: t("dashboard.My Work"),
            mobile: "primary",
            onClick: () => toggleContext("my-work"),
            active: contextMode === "my-work" && (isMobile ? isMobileExplorerOpen : desktopContextVisible),
        },
        {
            icon: "star",
            label: t("dashboard.Starred"),
            onClick: () => navigate(ROUTES.DASHBOARD.PROJECTS.STARRED, { smooth: true }),
            active: pageType === "projects" && tabName === "starred",
        },
        {
            icon: "history",
            label: t("dashboard.Recent work"),
            onClick: () => navigate(ROUTES.DASHBOARD.PROJECTS.RECENT, { smooth: true }),
            active: pageType === "projects" && tabName === "recent",
        },
        { icon: "clock", label: t("dashboard.Tracking"), onClick: headerNavs[3].onClick!, active: pageType === "tracking" },
        {
            icon: "inbox",
            label: t("dashboard.personalInbox.title"),
            mobile: "primary",
            onClick: () => navigate(ROUTES.DASHBOARD.INBOX),
            active: pageType === "inbox",
        },
        ...pinnedApps.items,
        { icon: "store", label: t("settings.App store"), mobile: "add", onClick: () => navigate(ROUTES.APPS.ROUTE) },
        {
            icon: "plus",
            label: t("dashboard.Create New Project"),
            onClick: () => navigate(`${location.pathname}/new-project`),
        },
        {
            icon: "history",
            label: t("dashboard.My Activity"),
            onClick: () => navigate(`${location.pathname}/my-activity`),
        },
        {
            icon: "settings",
            label: t("settings.App settings"),
            onClick: () => navigate(ROUTES.SETTINGS.ROUTE, { smooth: true }),
            placement: "bottom",
        },
    ];

    return (
        <DashboardStyledLayout
            headerNavs={headerNavs}
            headerTitle={
                pageType === "inbox"
                    ? t("dashboard.personalInbox.title")
                    : pageType === "cards"
                      ? t("dashboard.Cards")
                      : pageType === "tracking"
                        ? t("dashboard.Tracking")
                        : pageType === "my-work"
                          ? t("dashboard.My Work")
                          : t("dashboard.Projects")
            }
            activityRailItems={dashboardRailItems}
            workbenchContext={
                contextMode === "my-work" ? (
                    <div className="h-full overflow-y-auto">
                        <MyWorkPage compact onNavigate={() => isMobile && setIsMobileExplorerOpen(false)} />
                    </div>
                ) : (
                    <ProjectExplorerSidebar
                        active={isMobile ? isMobileExplorerOpen : isExplorerOpen}
                        onNavigate={() => isMobile && setIsMobileExplorerOpen(false)}
                    />
                )
            }
            workbenchContextHidden={isMobile || !isExplorerOpen}
            mobileWorkbenchContext={
                isMobile && isMobileExplorerOpen
                    ? {
                          title: contextMode === "my-work" ? t("dashboard.My Work") : t("common.Explorer"),
                          icon: contextMode === "my-work" ? "list-checks" : "panel-left",
                          onClose: () => setIsMobileExplorerOpen(false),
                      }
                    : undefined
            }
            scrollAreaMutable={scrollAreaMutable}
            className="overflow-x-hidden"
        >
            {currentUser ? <DashboardProvider currentUser={currentUser}>{pageContent}</DashboardProvider> : skeletonContent}
        </DashboardStyledLayout>
    );
});

export default DashboardProxy;
