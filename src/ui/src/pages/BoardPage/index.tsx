import { signalInboxSelection } from "./components/board/SignalInboxPanel";
import { BoardViewportProvider } from "@/core/providers/BoardViewportProvider";
import { hasPanelAccess } from "@/core/apps/PanelAccess";
import usePinnedAppRail from "@/core/apps/usePinnedAppRail";
import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import BoardPaneLayout from "./BoardPaneLayout";
import { useBoardSplitContext, useBoardSplitPane } from "@/core/providers/BoardSplitProvider";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Skeleton from "@/components/base/Skeleton";
import Flex from "@/components/base/Flex";
import BoardFloatingNavigation from "@/pages/BoardPage/components/board/BoardFloatingNavigation";
import { useFlippedCards } from "@/pages/BoardPage/components/card/CardFlipStore";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import { ROUTES } from "@/core/routing/constants";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useIsBoardChatAvailableHandlers from "@/controllers/socket/board/chat/useIsBoardChatAvailableHandlers";
import { useSocket } from "@/core/providers/SocketProvider";
import { useAuth } from "@/core/providers/AuthProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import BoardLoadError from "./BoardLoadError";
import BoardPage from "@/pages/BoardPage/BoardPage";
import SuspenseComponent from "@/components/base/SuspenseComponent";
import { IHeaderNavItem } from "@/components/Header/types";
import { SkeletonBoardSettingsPage } from "@/pages/BoardPage/SkeletonBoardSettingsPage";
import { TBoardViewType, useBoardController } from "@/core/providers/BoardController";
import useBoardAssignedUsersUpdatedHandlers from "@/controllers/socket/board/useBoardAssignedUsersUpdatedHandlers";
import useProjectDeletedHandlers from "@/controllers/socket/shared/useProjectDeletedHandlers";
import useProjectAccessRevokedHandlers from "@/controllers/socket/shared/useProjectAccessRevokedHandlers";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { SkeletonBoard } from "@/pages/BoardPage/components/board/Board";
import useBoardAssignedInternalBotChangedHandlers from "@/controllers/socket/board/useBoardAssignedInternalBotChangedHandlers";
import useInternalBotUpdatedHandlers from "@/controllers/socket/global/useInternalBotUpdatedHandlers";
import useSwitchSocketHandlers from "@/core/hooks/useSwitchSocketHandlers";
import useAuthStore from "@/core/stores/AuthStore";
import { InternalBotModel, Project, ProjectCard } from "@/core/models";
import { EHttpStatus, ESocketTopic } from "@langboard/core/enums";
import useBoardBotStatusMapHandlers from "@/controllers/socket/board/useBoardBotStatusMapHandlers";
import { BoardBotScopeList } from "@/pages/BoardPage/components/board/BoardBotScope";
import useGetProject from "@/controllers/api/board/useGetProject";
import useResolveCardLocation from "@/controllers/api/card/useResolveCardLocation";
import useGetCards from "@/controllers/api/board/useGetCards";
import useBoardUnreadCards from "@/pages/BoardPage/components/board/BoardUnreadCards";
import useGetGraphApprovalCount from "@/controllers/api/board/graphApprovals/useGetGraphApprovalCount";
import ActivityList from "@/components/ActivityList";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler from "@/core/helpers/SocketHandler";
import useBoardAppCatalog from "@/controllers/api/board/useBoardAppCatalog";
import { deletePanelState, invalidatePanelSessions } from "@/core/apps/PanelSession";
import AppPanel from "@/pages/BoardPage/components/apps/AppPanel";

import { cn } from "@/core/utils/ComponentUtils";
import useCardRelationshipsUpdatedHandlers from "@/controllers/socket/card/useCardRelationshipsUpdatedHandlers";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ProjectRole } from "@/core/models/roles";
import { ScreenMap } from "@/core/utils/VariantUtils";
import useResizeEvent from "@/core/hooks/useResizeEvent";
import useBoardBotScopeCreatedHandlers from "@/controllers/socket/board/botScopes/useBoardBotScopeCreatedHandlers";
import useBoardBotScopeDeletedHandlers from "@/controllers/socket/board/botScopes/useBoardBotScopeDeletedHandlers";
import useBoardBotScopeFreezeUpdatedHandlers from "@/controllers/socket/board/botScopes/useBoardBotScopeFreezeUpdatedHandlers";
import useBoardBotScopeTriggerConditionsUpdatedHandlers from "@/controllers/socket/board/botScopes/useBoardBotScopeTriggerConditionsUpdatedHandlers";
import useBoardBotCronRescheduledHandlers from "@/controllers/socket/board/botSchedules/useBoardBotCronRescheduledHandlers";
import useBoardBotCronScheduledHandlers from "@/controllers/socket/board/botSchedules/useBoardBotCronScheduledHandlers";
import useBoardBotCronUnscheduledHandlers from "@/controllers/socket/board/botSchedules/useBoardBotCronUnscheduledHandlers";
import useBoardGraphApprovalDeletedHandlers from "@/controllers/socket/board/graphApprovals/useBoardGraphApprovalDeletedHandlers";
import useBoardGraphApprovalRequestedHandlers from "@/controllers/socket/board/graphApprovals/useBoardGraphApprovalRequestedHandlers";
import useBoardGraphApprovalUpdatedHandlers from "@/controllers/socket/board/graphApprovals/useBoardGraphApprovalUpdatedHandlers";
import { useWorkbenchContextOpen, useUserSettings } from "@/core/stores/UserSettingsStore";
import ProjectExplorerSidebar from "@/pages/DashboardPage/components/ProjectExplorerSidebar";
import {
    WORKBENCH_OPEN_CHANGES_EVENT,
    WORKBENCH_OPEN_MY_WORK_EVENT,
    WORKBENCH_OPEN_RELATIONS_EVENT,
    WORKBENCH_TOGGLE_CONTEXT_EVENT,
} from "@/pages/DashboardPage/components/WorkbenchCommands";
import { currentMyWorkWorkspaceFilter, type MyWorkWorkspaceSelection } from "./components/board/MyWorkWorkspaceFilter";
import type { MyWorkFilter } from "@/pages/DashboardPage/my-work-context/MyWorkContext";
import { closeProject } from "@/pages/DashboardPage/components/OpenCardsStore";

const BoardAutomationRuns = lazy(() => import("./BoardAutomationRuns"));
const ChatSidebar = lazy(() => import("@/pages/BoardPage/components/chat/ChatSidebar"));
const BoardCardPage = lazy(() => import("@/pages/BoardPage/BoardCardPage"));
const BoardSettingsPage = lazy(() => import("@/pages/BoardPage/BoardSettingsPage"));
const BoardGraphPage = lazy(() => import("@/pages/BoardPage/BoardGraphPage"));
const BoardWikiPage = lazy(() => import("@/pages/BoardPage/BoardWikiPage"));
const BoardSignalInbox = lazy(() => import("@/pages/BoardPage/components/board/BoardSignalInbox"));
const BoardChangesSidebar = lazy(() => import("@/pages/BoardPage/components/board/BoardChangesSidebar"));
const MyWorkSidebar = lazy(() => import("@/pages/DashboardPage/MyWorkPage"));
const BoardRelationsSidebar = lazy(() => import("@/pages/BoardPage/components/board/BoardRelationsSidebar"));
const BoardOutlineSidebar = lazy(() => import("@/pages/BoardPage/components/board/BoardOutlineSidebar"));
const BoardCloud = lazy(() => import("@/pages/BoardPage/components/cloud/BoardCloud"));
const BoardMcpConnection = lazy(() => import("./components/mcp/BoardMcpConnection"));
const BoardCloudPage = lazy(() => import("@/pages/BoardPage/BoardCloudPage"));
const BoardScheduleSidebar = lazy(() => import("@/pages/BoardPage/components/board/BoardScheduleSidebar"));
const BoardWikiSidebar = lazy(() => import("@/pages/BoardPage/components/board/BoardWikiSidebar"));
const SkeletonBoardWikiPage = () => <Skeleton className="m-4 h-48 w-[min(36rem,calc(100%-2rem))]" />;

const getCurrentPage = (pageRoute?: string): TBoardViewType => {
    switch (pageRoute) {
        case "card":
            return "card";
        case "wiki":
            return "wiki";
        case "cloud":
            return "cloud";
        case "graph":
            return "graph";
        case "settings":
            return "settings";
        default:
            return "board";
    }
};

type TBoardSidePanel = "botScope" | "switchProject";
type TWorkbenchContext =
    | "explorer"
    | "my-work"
    | "changes"
    | "inbox"
    | "activity"
    | "relations"
    | "outline"
    | "wiki"
    | "schedule"
    | "cloud"
    | "mcp"
    | "chat"
    | "app";

const BoardProxy = memo((): React.JSX.Element => {
    const { setPageAliasRef } = usePageHeader();
    const split = useBoardSplitContext();
    const paneID = useBoardSplitPane();
    const focused = !split || !paneID || split.focusedPane === paneID;
    const { currentUser } = useAuth();
    const socket = useSocket();
    const navigate = usePageNavigateRef();
    const location = usePageLocation();
    const [projectUID, pageRoute] = location.pathname.split("/").slice(2);
    if (!projectUID) {
        return <Navigate to={ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND)} replace />;
    }

    const isCardRoute = !!pageRoute && !["graph", "wiki", "cloud", "settings"].includes(pageRoute);
    const cardLocation = useResolveCardLocation(projectUID, isCardRoute ? pageRoute : undefined, paneID, location.key);
    const canonicalProjectUID = cardLocation.data?.canonical_project_uid;
    const locationReady = !isCardRoute || canonicalProjectUID === projectUID;
    const redirectHistory = useRef<{ cardUID: string; actorUID?: string; projects: Set<string> }>({ cardUID: "", projects: new Set() });
    if (redirectHistory.current.cardUID !== pageRoute || redirectHistory.current.actorUID !== currentUser?.uid) {
        redirectHistory.current = { cardUID: pageRoute, actorUID: currentUser?.uid, projects: new Set() };
    }
    const locationLoop = !!canonicalProjectUID && canonicalProjectUID !== projectUID && redirectHistory.current.projects.has(canonicalProjectUID);
    useEffect(() => {
        if (!isCardRoute || !canonicalProjectUID || locationLoop) return;
        if (canonicalProjectUID === projectUID) {
            redirectHistory.current.projects.clear();
            return;
        }
        redirectHistory.current.projects.add(projectUID);
        navigate(`${ROUTES.BOARD.CARD(canonicalProjectUID, pageRoute)}${location.search}${location.hash}`, { replace: true, state: location.state });
    }, [canonicalProjectUID, isCardRoute, projectUID, pageRoute, locationLoop, location.search, location.hash, location.state, navigate]);

    const session = useAuthStore((state) => state.getSessionVersion());
    const projectScope = JSON.stringify([projectUID, currentUser?.uid, session]);
    const acceptedProjectScope = useRef<string | undefined>(undefined);
    // Resolving a card must not disable the already authorized board query or
    // unmount its realtime owners. A different board or session still starts cold.
    const retainBoard = acceptedProjectScope.current === projectScope;
    const { data, isFetching, error, refetch } = useGetProject(
        { uid: projectUID },
        { enabled: locationReady || retainBoard, placeholderData: isCardRoute ? undefined : keepPreviousData }
    );
    const { send: sendBoardBotStatusMap } = useBoardBotStatusMapHandlers({ projectUID });
    const hasCurrentProject = (locationReady || retainBoard) && !!data && data.project.uid === projectUID;
    if (hasCurrentProject && !error) acceptedProjectScope.current = projectScope;

    useEffect(() => {
        if (!error || !locationReady) {
            return;
        }

        const { handle } = setupApiErrorHandler({
            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                after: () => {
                    if (currentUser) closeProject(currentUser.uid, projectUID);
                    navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true });
                },
            },
            [EHttpStatus.HTTP_404_NOT_FOUND]: {
                after: () => {
                    if (currentUser) closeProject(currentUser.uid, projectUID);
                    navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND), { replace: true });
                },
            },
        });

        handle(error);
    }, [error, locationReady]);

    useEffect(() => {
        if (!focused) return;
        if (!data || !hasCurrentProject) {
            setPageAliasRef.current();
            return;
        }

        if (pageRoute !== "card") {
            setPageAliasRef.current(data.project.title);
        }
    }, [data, hasCurrentProject, pageRoute, focused]);

    useEffect(() => {
        if (!hasCurrentProject) {
            return;
        }

        socket.subscribe(ESocketTopic.Board, [projectUID], () => {
            sendBoardBotStatusMap({});
        });
        socket.subscribe(ESocketTopic.BoardSettings, [projectUID]);

        return () => {
            socket.unsubscribe(ESocketTopic.Board, [projectUID]);
            socket.unsubscribe(ESocketTopic.BoardSettings, [projectUID]);
        };
    }, [hasCurrentProject, projectUID]);

    if (!locationReady && (!hasCurrentProject || cardLocation.error || locationLoop)) {
        return cardLocation.error || locationLoop ? (
            <BoardLoadError isFetching={cardLocation.isFetching} retry={() => void cardLocation.refetch()} />
        ) : (
            <SkeletonBoard />
        );
    }

    if (!data) {
        return error ? <BoardLoadError isFetching={isFetching} retry={() => void refetch()} /> : <SkeletonBoard />;
    }

    return (
        <BoardProxyDisplay
            project={data.project}
            pageRoute={locationReady ? pageRoute : ""}
            isFetching={isFetching}
            isProjectLoading={data.project.uid !== projectUID}
        />
    );
});

interface IBoardProxyDisplayProps {
    project: Project.TModel;
    pageRoute: string;
    isFetching: boolean;
    isProjectLoading: boolean;
}

function BoardHeaderProjectTitle({ project }: { project: Project.TModel }) {
    const title = project.useField("title");
    const split = useBoardSplitContext();
    const paneID = useBoardSplitPane();
    const focused = !split || !paneID || split.focusedPane === paneID;
    const { setPageAliasRef } = usePageHeader();
    useEffect(() => {
        if (focused) setPageAliasRef.current(title);
    }, [title, setPageAliasRef, focused]);
    return <span className="max-w-32 shrink-0 truncate">{title}</span>;
}

function BoardHeaderCardTitle({ card }: { card: ProjectCard.TModel }) {
    const title = card.useField("title");
    return <span className="min-w-0 truncate">{title}</span>;
}

function BoardProxyDisplay({ pageRoute, isFetching, isProjectLoading, project }: IBoardProxyDisplayProps): React.JSX.Element {
    const boardTitle = project.useField("title");
    const split = useBoardSplitContext();
    const paneID = useBoardSplitPane();
    const focused = !split || !paneID || split.focusedPane === paneID;
    const [t] = useTranslation();
    const socket = useSocket();
    const { currentUser } = useAuth();
    const flippedCards = useFlippedCards(currentUser?.uid ?? "", project.uid);
    const navigate = usePageNavigateRef();
    const myWorkWorkspaceRef = useRef<HTMLDivElement>(null);
    const identityVersion = useAuthStore((state) => state.getIdentityVersion());
    const [myWorkSelection, setMyWorkSelection] = useState<MyWorkWorkspaceSelection | null>(null);
    const myWorkFilter = currentMyWorkWorkspaceFilter(myWorkSelection, currentUser?.uid, identityVersion, project.uid);
    const selectMyWorkFilter = (filter: MyWorkFilter) => {
        if (!currentUser) return;
        setMyWorkSelection({ actorUID: currentUser.uid, identityVersion, projectUID: project.uid, filter });
        if (isMobile) setActiveSidePanel(undefined);
    };
    const [isCardExpanded, setIsCardExpanded] = useState(false);
    const [nowWorkOpen, setNowWorkOpen] = useState(false);
    const nowWorkToggleRef = useRef<HTMLDivElement>(null);
    const [activeSidePanel, setActiveSidePanel] = useState<TBoardSidePanel>();
    const [chatPanelOpened, setChatPanelOpened] = useState(false);
    const { data: appCatalog, isError: appCatalogError } = useBoardAppCatalog(project.uid, currentUser?.uid);
    const [selectedAppKey, setSelectedAppKey] = useState<string>();
    const panelApps = (appCatalogError ? [] : (appCatalog ?? [])).filter(hasPanelAccess);
    const selectedApp = panelApps.find((app) => app.key === selectedAppKey);
    const previousPanelKeys = useRef<{ scope: string; keys: string[] }>({ scope: "", keys: [] });
    useEffect(() => {
        const scope = JSON.stringify([currentUser?.uid, project.uid]);
        const currentKeys = panelApps.map((app) => JSON.stringify([currentUser?.uid, project.uid, app.key, app.version]));
        for (const key of previousPanelKeys.current.scope === scope ? previousPanelKeys.current.keys : []) {
            if (!currentKeys.includes(key)) deletePanelState(key);
        }
        previousPanelKeys.current = { scope, keys: currentKeys };
    }, [appCatalog, appCatalogError, currentUser?.uid, project.uid]);
    const handoffLocation = usePageLocation();
    const handoffSignal = signalInboxSelection(handoffLocation.search);
    const [workbenchContextMode, setWorkbenchContextMode] = useState<TWorkbenchContext>("explorer");
    const workbenchContextTitle = {
        explorer: t("common.Explorer"),
        "my-work": t("dashboard.My Work"),
        changes: t("dashboard.Changes"),
        inbox: t("card.inbox.title"),
        activity: t("board.Activity"),
        relations: t("dashboard.Relations"),
        outline: t("dashboard.Outline"),
        wiki: t("board.Wiki"),
        schedule: t("dashboard.Schedule"),
        cloud: t("cloud.Cloud"),
        mcp: t("mcp.Langboard connection"),
        chat: t("project.Chat with AI"),
        app: selectedApp?.panel?.name ?? t("project.App panel"),
    }[workbenchContextMode];
    const [isContextOpen, setIsContextOpen] = useWorkbenchContextOpen(currentUser?.uid);
    const [isMobile, setIsMobile] = useState(window.innerWidth < ScreenMap.size.md);
    useEffect(() => {
        if (!isMobile || !myWorkFilter) return;
        const frame = requestAnimationFrame(() => myWorkWorkspaceRef.current?.focus({ preventScroll: true }));
        return () => cancelAnimationFrame(frame);
    }, [isMobile, myWorkFilter]);
    const isBotScopeOpened = activeSidePanel === "botScope";
    const collapsedPanels = useUserSettings("workbench_sidebar_collapsed");
    const isWorkbenchContextVisible =
        !isBotScopeOpened && (isMobile ? activeSidePanel === "switchProject" : isContextOpen && !collapsedPanels?.[currentUser?.uid ?? ""]);
    const toggleBotScope = useCallback(() => {
        setActiveSidePanel((value) => (value === "botScope" ? undefined : "botScope"));
        if (!isMobile) setIsContextOpen(true);
    }, [isMobile, setIsContextOpen]);
    const toggleSwitchProject = useCallback(() => {
        if (isMobile) {
            setActiveSidePanel((value) => (value === "switchProject" ? undefined : "switchProject"));
        } else if (isBotScopeOpened) {
            setActiveSidePanel(undefined);
            setIsContextOpen(true);
        } else {
            setIsContextOpen((open) => !open);
        }
    }, [isMobile, isBotScopeOpened, setIsContextOpen]);
    const showWorkbenchContext = useCallback(
        (mode: TWorkbenchContext) => {
            setWorkbenchContextMode(mode);
            if (mode === "chat") setChatPanelOpened(true);
            if (!isMobile) setIsContextOpen(true);
            setActiveSidePanel(isMobile ? "switchProject" : undefined);
        },
        [isMobile, setIsContextOpen]
    );
    useEffect(() => {
        if (handoffSignal) showWorkbenchContext("inbox");
    }, [handoffSignal, project.uid, currentUser?.uid, showWorkbenchContext]);
    const toggleWorkbenchContext = (mode: TWorkbenchContext) => {
        if (isWorkbenchContextVisible && workbenchContextMode === mode) {
            if (isMobile) setActiveSidePanel(undefined);
            else setIsContextOpen(false);
        } else showWorkbenchContext(mode);
    };
    const pinnedApps = usePinnedAppRail({
        userUID: currentUser?.uid,
        projectUID: project.uid,
        activeKey: isWorkbenchContextVisible && workbenchContextMode === "app" ? selectedAppKey : undefined,
        onLaunch: (key) => {
            if (!panelApps.some((app) => app.key === key)) return false;
            setSelectedAppKey(key);
            showWorkbenchContext("app");
            return true;
        },
    });
    useEffect(() => {
        if (!focused) return;
        const toggleContext = () => toggleSwitchProject();
        const openChanges = () => showWorkbenchContext("changes");
        const openMyWork = () => showWorkbenchContext("my-work");
        const openRelations = () => showWorkbenchContext("relations");
        window.addEventListener(WORKBENCH_TOGGLE_CONTEXT_EVENT, toggleContext);
        window.addEventListener(WORKBENCH_OPEN_CHANGES_EVENT, openChanges);
        window.addEventListener(WORKBENCH_OPEN_MY_WORK_EVENT, openMyWork);
        window.addEventListener(WORKBENCH_OPEN_RELATIONS_EVENT, openRelations);
        return () => {
            window.removeEventListener(WORKBENCH_TOGGLE_CONTEXT_EVENT, toggleContext);
            window.removeEventListener(WORKBENCH_OPEN_CHANGES_EVENT, openChanges);
            window.removeEventListener(WORKBENCH_OPEN_MY_WORK_EVENT, openMyWork);
            window.removeEventListener(WORKBENCH_OPEN_RELATIONS_EVENT, openRelations);
        };
    }, [toggleSwitchProject, showWorkbenchContext, focused]);
    const { boardViewType, selectCardViewType, chatSidebarRef, boardChat, setBoardViewType, setChatResizableSidebar, setBoardChat } =
        useBoardController();
    const isCardPage = !!pageRoute && !["graph", "wiki", "cloud", "settings"].includes(pageRoute);
    // BoardPage owns fetching; the shell observes its current account/session snapshot.
    const { data: boardCardsData, dataUpdatedAt: boardCardsUpdatedAt } = useGetCards({ project_uid: project.uid }, { enabled: false });
    const unreadCards = useBoardUnreadCards(project.uid, currentUser && boardCardsData && !isProjectLoading ? boardCardsUpdatedAt : 0);
    const activeCard = boardCardsData && isCardPage ? ProjectCard.Model.getModel(pageRoute) : undefined;
    const { data: approvalCountData, refetch: refetchApprovalCount } = useGetGraphApprovalCount(project.uid, {
        interceptToast: false,
        enabled: !isProjectLoading,
    });
    const refreshApprovalCount = useCallback(() => void refetchApprovalCount(), [refetchApprovalCount]);
    const graphApprovalRequestedHandlers = useBoardGraphApprovalRequestedHandlers({ projectUID: project.uid, callback: refreshApprovalCount });
    const graphApprovalUpdatedHandlers = useBoardGraphApprovalUpdatedHandlers({ projectUID: project.uid, callback: refreshApprovalCount });
    const graphApprovalDeletedHandlers = useBoardGraphApprovalDeletedHandlers({ projectUID: project.uid, callback: refreshApprovalCount });
    const pendingGraphApprovalCount = approvalCountData?.count ?? 0;
    const pendingGraphApprovalBadge = pendingGraphApprovalCount > 99 ? "99+" : pendingGraphApprovalCount || undefined;
    const isBoardChatAvailableHandlers = useMemo(
        () =>
            useIsBoardChatAvailableHandlers({
                projectUID: project.uid,
                callback: (result) => {
                    if (result.available) {
                        setBoardChat({
                            bot: result.bot,
                            projectUID: project.uid,
                        });
                        setChatResizableSidebar(() => ({
                            children: null,
                            initialWidth: 280,
                            collapsableWidth: 210,
                            floatingIcon: "message-circle",
                            floatingTitle: t("project.Chat with AI"),
                            floatingFullScreen: true,
                            widthCssVariable: "--board-chat-sidebar-width",
                            hidden: true,
                        }));
                    } else {
                        setBoardChat(undefined);
                        setChatResizableSidebar(() => ({
                            children: <></>,
                            initialWidth: 280,
                            collapsableWidth: 210,
                            hidden: true,
                        }));
                    }
                },
            }),
        [project, setBoardChat, setChatResizableSidebar]
    );
    const boardAssignedUsersUpdatedHandlers = useMemo(
        () =>
            useBoardAssignedUsersUpdatedHandlers({
                projectUID: project.uid,
                callback: (result) => {
                    if (!currentUser || (!result.assigned_user_uids.includes(currentUser.uid) && !currentUser.is_admin)) {
                        Toast.Add.error(t("errors.Forbidden"));
                    }
                },
            }),
        [project, currentUser]
    );
    const projectDeletedHandlers = useMemo(
        () =>
            useProjectDeletedHandlers({
                topic: ESocketTopic.Board,
                projectUID: project.uid,
                callback: () => {
                    if (currentUser) closeProject(currentUser.uid, project.uid);
                    Toast.Add.error(t("project.errors.Project closed."));
                    navigate(ROUTES.DASHBOARD.PROJECTS.ALL, { replace: true });
                },
            }),
        [project]
    );
    const projectAccessRevokedHandlers = useMemo(
        () =>
            useProjectAccessRevokedHandlers({
                topic: ESocketTopic.Board,
                projectUID: project.uid,
                callback: () => {
                    navigate(ROUTES.DASHBOARD.PROJECTS.ALL, { replace: true });
                },
            }),
        [project]
    );
    const boardAssignedInternalBotChangedHandlers = useMemo(
        () =>
            useBoardAssignedInternalBotChangedHandlers({
                projectUID: project.uid,
                callback: (data) => {
                    const internalBot = InternalBotModel.Model.getModel(data.internal_bot_uid);
                    if (internalBot) {
                        const existingBots = [...project.internal_bots];
                        const targetBotIndex = existingBots.findIndex((bot) => bot.bot_type === internalBot.bot_type);
                        if (targetBotIndex !== -1 && existingBots[targetBotIndex].uid !== internalBot.uid) {
                            existingBots.splice(targetBotIndex, 1);
                        }
                        existingBots.push(internalBot);
                        project.internal_bots = existingBots;
                    }

                    if (internalBot && internalBot.bot_type !== InternalBotModel.EInternalBotType.ProjectChat) {
                        return;
                    }

                    isBoardChatAvailableHandlers.send({});
                },
            }),
        [project, isBoardChatAvailableHandlers]
    );
    const internalBotUpdatedHandlers = useMemo(
        () =>
            useInternalBotUpdatedHandlers({
                callback: (data) => {
                    const internalBot = InternalBotModel.Model.getModel(data.uid);
                    if (internalBot && internalBot.bot_type !== InternalBotModel.EInternalBotType.ProjectChat) {
                        return;
                    }

                    isBoardChatAvailableHandlers.send({});
                },
            }),
        [isBoardChatAvailableHandlers]
    );
    const cardRelationshipsUpdatedHandlers = useMemo(
        () =>
            useCardRelationshipsUpdatedHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotScopeCreatedHandlers = useMemo(
        () =>
            useBoardBotScopeCreatedHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotScopeTriggerConditionsUpdatedHandlers = useMemo(
        () =>
            useBoardBotScopeTriggerConditionsUpdatedHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotScopeFreezeUpdatedHandlers = useMemo(
        () =>
            useBoardBotScopeFreezeUpdatedHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotScopeDeletedHandlers = useMemo(
        () =>
            useBoardBotScopeDeletedHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotCronScheduledHandlers = useMemo(
        () =>
            useBoardBotCronScheduledHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotCronRescheduledHandlers = useMemo(
        () =>
            useBoardBotCronRescheduledHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardBotCronUnscheduledHandlers = useMemo(
        () =>
            useBoardBotCronUnscheduledHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const appQueryClient = useQueryClient();
    const appRegistryChangedHandlers = useMemo(
        () =>
            useSocketHandler({
                topic: ESocketTopic.Global,
                eventKey: "app-registry-changed",
                onProps: {
                    name: SocketEvents.SERVER.GLOBALS.APP_REGISTRY_CHANGED,
                    callback: () => {
                        invalidatePanelSessions();
                        setSelectedAppKey(undefined);
                        void appQueryClient.invalidateQueries({ queryKey: ["board-app-catalog"] });
                    },
                },
            }),
        [appQueryClient]
    );
    const handlers = useMemo(
        () => [
            isBoardChatAvailableHandlers,
            boardAssignedUsersUpdatedHandlers,
            projectDeletedHandlers,
            projectAccessRevokedHandlers,
            boardAssignedInternalBotChangedHandlers,
            internalBotUpdatedHandlers,
            appRegistryChangedHandlers,
            cardRelationshipsUpdatedHandlers,
            boardBotScopeCreatedHandlers,
            boardBotScopeTriggerConditionsUpdatedHandlers,
            boardBotScopeFreezeUpdatedHandlers,
            boardBotScopeDeletedHandlers,
            boardBotCronScheduledHandlers,
            boardBotCronRescheduledHandlers,
            boardBotCronUnscheduledHandlers,
            graphApprovalRequestedHandlers,
            graphApprovalUpdatedHandlers,
            graphApprovalDeletedHandlers,
        ],
        [
            isBoardChatAvailableHandlers,
            boardAssignedUsersUpdatedHandlers,
            projectDeletedHandlers,
            projectAccessRevokedHandlers,
            boardAssignedInternalBotChangedHandlers,
            internalBotUpdatedHandlers,
            appRegistryChangedHandlers,
            cardRelationshipsUpdatedHandlers,
            boardBotScopeCreatedHandlers,
            boardBotScopeTriggerConditionsUpdatedHandlers,
            boardBotScopeFreezeUpdatedHandlers,
            boardBotScopeDeletedHandlers,
            boardBotCronScheduledHandlers,
            boardBotCronRescheduledHandlers,
            boardBotCronUnscheduledHandlers,
            graphApprovalRequestedHandlers,
            graphApprovalUpdatedHandlers,
            graphApprovalDeletedHandlers,
        ]
    );

    const { subscribedTopics } = useSwitchSocketHandlers({ socket, handlers });

    useResizeEvent(
        {
            doneCallback: () => {
                setIsMobile(window.innerWidth < ScreenMap.size.md);
            },
        },
        [setIsMobile]
    );

    useEffect(() => {
        if (isFetching || !subscribedTopics.includes(ESocketTopic.Board)) {
            return;
        }

        isBoardChatAvailableHandlers.send({});
    }, [isFetching, subscribedTopics]);

    useEffect(() => {
        setBoardViewType(getCurrentPage(pageRoute));
    }, [pageRoute]);

    useEffect(() => {
        setIsCardExpanded(false);
    }, [pageRoute]);

    const headerNavs: IHeaderNavItem[] = [
        {
            name: t("board.Board"),
            onClick: () => {
                setBoardViewType("board");
                navigate(ROUTES.BOARD.MAIN(project.uid), { smooth: true });
            },
            active: boardViewType === "board" || boardViewType === "card",
            hidden: !!selectCardViewType,
        },
        {
            name: t("board.Wiki"),
            onClick: () => {
                setBoardViewType("wiki");
                navigate(ROUTES.BOARD.WIKI(project.uid), { smooth: true });
            },
            active: boardViewType === "wiki",
            hidden: !!selectCardViewType,
        },
        {
            name: t("board.Relationship graph"),
            onClick: () => {
                setBoardViewType("graph");
                navigate(ROUTES.BOARD.GRAPH(project.uid), { smooth: true });
            },
            active: boardViewType === "graph",
            hidden: !!selectCardViewType,
        },
        {
            name: t("board.Activity"),
            onClick: () => showWorkbenchContext("activity"),
            active: workbenchContextMode === "activity" && isWorkbenchContextVisible,
            hidden: !!selectCardViewType,
        },
        {
            name: t("board.Settings"),
            onClick: () => {
                setBoardViewType("settings");
                navigate(ROUTES.BOARD.SETTINGS(project.uid), { smooth: true });
            },
            active: boardViewType === "settings",
            hidden: !!selectCardViewType,
        },
        {
            name: t("bot.Scope bot"),
            onClick: toggleBotScope,
            active: isBotScopeOpened,
            hidden: !!selectCardViewType && !!currentUser && currentUser.is_admin,
        },
    ];
    headerNavs.push({
        name: t("cloud.Cloud"),
        onClick: () => navigate(ROUTES.BOARD.CLOUD(project.uid)),
        active: boardViewType === "cloud",
        hidden: !!selectCardViewType,
    });
    let PageComponent;
    let SkeletonComponent;
    // Route-backed pages must win during the render that observes a location
    // change. Waiting for the boardViewType effect leaves the previous Wiki
    // tree mounted for one render, where its auto-selection can overwrite a
    // card deep link or the bare board route and navigate back to the Wiki.
    const renderedViewType = getCurrentPage(pageRoute);
    switch (renderedViewType) {
        case "graph":
            PageComponent = BoardGraphPage;
            SkeletonComponent = SkeletonBoard;
            break;
        case "wiki":
            PageComponent = BoardWikiPage;
            SkeletonComponent = SkeletonBoardWikiPage;
            break;
        case "cloud":
            PageComponent = BoardCloudPage;
            SkeletonComponent = SkeletonBoardWikiPage;
            break;
        case "settings":
            PageComponent = BoardSettingsPage;
            SkeletonComponent = SkeletonBoardSettingsPage;
            break;
        default:
            PageComponent = BoardPage;
            SkeletonComponent = SkeletonBoard;
            break;
    }

    return (
        <>
            <BoardPaneLayout
                boardTitle={boardTitle}
                workbenchStatus={
                    !isProjectLoading && currentUser && boardCardsData
                        ? {
                              userUID: currentUser.uid,
                              sessionVersion: useAuthStore.getState().getSessionVersion(),
                              card: activeCard?.project_uid === project.uid ? activeCard : undefined,
                              project,
                              view: {
                                  board: t("board.Board"),
                                  card: t("board.Board"),
                                  wiki: t("board.Wiki"),
                                  graph: t("board.Relationship graph"),
                                  cloud: t("cloud.Cloud"),
                                  settings: t("board.Settings"),
                              }[renderedViewType],
                              unreadCount: boardCardsData ? unreadCards.length : undefined,
                          }
                        : undefined
                }
                inert={isProjectLoading}
                aria-busy={isProjectLoading}
                headerNavs={[
                    ...headerNavs,
                    {
                        name: t("dashboard.Relations panel"),
                        onClick: () => showWorkbenchContext("relations"),
                        active: workbenchContextMode === "relations",
                    },
                    {
                        name: t("dashboard.Outline"),
                        onClick: () => showWorkbenchContext("outline"),
                        active: workbenchContextMode === "outline",
                    },
                    {
                        name: t("dashboard.Wiki panel"),
                        onClick: () => showWorkbenchContext("wiki"),
                        active: workbenchContextMode === "wiki",
                    },
                ]}
                headerTitle={
                    <span className="flex min-w-0 items-center gap-1">
                        <BoardHeaderProjectTitle key={project.uid} project={project} />
                        {isCardPage && activeCard && (
                            <>
                                <IconComponent icon="chevron-right" size="3" className="shrink-0 text-muted-foreground" />
                                <BoardHeaderCardTitle key={activeCard.uid} card={activeCard} />
                            </>
                        )}
                    </span>
                }
                activityRailItems={[
                    {
                        icon: "panel-left",
                        label: t("common.Explorer"),
                        mobile: "primary",
                        onClick: () => toggleWorkbenchContext("explorer"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "explorer",
                    },
                    {
                        icon: "list-checks",
                        label: t("dashboard.My Work"),
                        mobile: "primary",
                        onClick: () => toggleWorkbenchContext("my-work"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "my-work",
                    },
                    {
                        icon: "inbox",
                        label: t("card.inbox.title"),
                        onClick: () => toggleWorkbenchContext("inbox"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "inbox",
                    },
                    {
                        icon: "circle-dot",
                        label: t("dashboard.Changes"),
                        badge: unreadCards.length || undefined,
                        onClick: () => toggleWorkbenchContext("changes"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "changes",
                    },
                    {
                        icon: "network",
                        label: t("dashboard.Relations"),
                        onClick: () => toggleWorkbenchContext("relations"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "relations",
                    },
                    {
                        icon: "list-tree",
                        label: t("dashboard.Outline"),
                        onClick: () => toggleWorkbenchContext("outline"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "outline",
                    },
                    {
                        icon: "notebook-pen",
                        label: t("board.Wiki"),
                        onClick: () => toggleWorkbenchContext("wiki"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "wiki",
                    },
                    {
                        icon: "calendar",
                        label: t("dashboard.Schedule"),
                        onClick: () => toggleWorkbenchContext("schedule"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "schedule",
                    },
                    {
                        label: t("project.Chat with AI"),
                        icon: "message-circle",
                        onClick: () => toggleWorkbenchContext("chat"),
                        active: workbenchContextMode === "chat" && isWorkbenchContextVisible,
                        hidden: !!selectCardViewType,
                    },
                    {
                        icon: "cloud",
                        label: t("cloud.Cloud"),
                        onClick: () => toggleWorkbenchContext("cloud"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "cloud",
                        hidden: !!selectCardViewType,
                    },
                    {
                        icon: "plug",
                        label: t("mcp.Langboard connection"),
                        onClick: () => toggleWorkbenchContext("mcp"),
                        active: isWorkbenchContextVisible && workbenchContextMode === "mcp",
                        hidden: !!selectCardViewType,
                    },
                    ...pinnedApps.items.map((item) => ({ ...item, hidden: !!selectCardViewType })),
                    {
                        icon: "store",
                        label: t("settings.App store"),
                        mobile: "add",
                        onClick: () => navigate(`${ROUTES.APPS.ROUTE}?project_uid=${project.uid}`),
                    },
                    ...headerNavs.map((nav, index) => ({
                        icon: ["columns-3", "notebook-pen", "network", "history", "settings", "bot", "cloud"][index],
                        label: String(nav.name),
                        onClick: nav.onClick!,
                        active: nav.active,
                        hidden: nav.hidden || index === 0 || index === 1 || index === 2 || index === 6,
                        placement: "bottom" as const,
                        badge: index === 5 ? pendingGraphApprovalBadge : undefined,
                    })),
                ]}
                workbenchContext={
                    <div className="size-full">
                        {chatPanelOpened && boardChat && (
                            <div
                                data-workbench-command-context="chat"
                                hidden={workbenchContextMode !== "chat" || isBotScopeOpened}
                                role="region"
                                aria-label={t("project.Chat with AI")}
                                className="h-full"
                            >
                                <Suspense fallback={<Skeleton className="m-3 h-24" />}>
                                    <ChatSidebar
                                        ref={chatSidebarRef}
                                        active={workbenchContextMode === "chat" && isWorkbenchContextVisible}
                                        onClose={isMobile ? () => setActiveSidePanel(undefined) : undefined}
                                    />
                                </Suspense>
                            </div>
                        )}
                        {workbenchContextMode === "app" &&
                        isWorkbenchContextVisible &&
                        !selectCardViewType &&
                        !isProjectLoading &&
                        currentUser &&
                        selectedApp ? (
                            <AppPanel
                                key={`${currentUser.uid}:${project.uid}:${selectedApp.key}`}
                                app={selectedApp}
                                projectUID={project.uid}
                                userUID={currentUser.uid}
                                onClose={() => {
                                    setIsContextOpen(false);
                                    setActiveSidePanel(undefined);
                                }}
                            />
                        ) : isBotScopeOpened ? (
                            <BoardBotScopeSidebar project={project} />
                        ) : workbenchContextMode === "explorer" ? (
                            <ProjectExplorerSidebar
                                active={isWorkbenchContextVisible}
                                currentProject={project}
                                onNavigate={() => setActiveSidePanel(undefined)}
                            />
                        ) : (
                            <div
                                data-workbench-command-context={workbenchContextMode}
                                hidden={workbenchContextMode === "chat" && !!boardChat}
                                tabIndex={-1}
                                role="region"
                                aria-label={workbenchContextTitle}
                                className="h-full outline-none"
                            >
                                <Suspense fallback={<Skeleton className="m-3 h-24" />}>
                                    {workbenchContextMode === "app" ? (
                                        <p className="p-3 text-sm" role="status">
                                            {t("project.App panel unavailable")}
                                        </p>
                                    ) : workbenchContextMode === "chat" ? (
                                        !boardChat && (
                                            <div role="status" className="space-y-3 p-4 text-sm text-muted-foreground">
                                                <p>{t("errors.Server has been temporarily disabled. Please try again later.")}</p>
                                                <Button variant="outline" size="sm" onClick={() => isBoardChatAvailableHandlers.send({})}>
                                                    {t("common.Retry")}
                                                </Button>
                                            </div>
                                        )
                                    ) : workbenchContextMode === "my-work" ? (
                                        <div className="h-full overflow-y-auto">
                                            <MyWorkSidebar
                                                key={`${currentUser?.uid}:${identityVersion}:${project.uid}`}
                                                compact
                                                projectUID={project.uid}
                                                filter={myWorkFilter ?? { projectUID: project.uid, group: null }}
                                                onFilterChange={selectMyWorkFilter}
                                                onNavigate={() => {
                                                    setMyWorkSelection(null);
                                                    if (isMobile) setActiveSidePanel(undefined);
                                                }}
                                            />
                                        </div>
                                    ) : workbenchContextMode === "schedule" ? (
                                        isWorkbenchContextVisible &&
                                        !isProjectLoading &&
                                        currentUser && (
                                            <BoardScheduleSidebar
                                                key={`${currentUser.uid}:${project.uid}`}
                                                project={project}
                                                onNavigate={() => isMobile && setActiveSidePanel(undefined)}
                                            />
                                        )
                                    ) : workbenchContextMode === "inbox" ? (
                                        <BoardSignalInbox
                                            key={`${currentUser?.uid}:${identityVersion}:${project.uid}:${handoffSignal ?? ""}`}
                                            project={project}
                                        />
                                    ) : workbenchContextMode === "changes" ? (
                                        <BoardChangesSidebar projectUID={project.uid} onNavigate={() => isMobile && setActiveSidePanel(undefined)} />
                                    ) : workbenchContextMode === "activity" && currentUser ? (
                                        <ActivityList
                                            key={project.uid}
                                            className="h-full"
                                            form={{ listType: "ActivityModel", type: "project", project_uid: project.uid }}
                                            currentUser={currentUser}
                                            outerClassName="h-full px-3"
                                        />
                                    ) : workbenchContextMode === "relations" ? (
                                        <BoardRelationsSidebar projectUID={project.uid} cardUID={activeCard?.uid} />
                                    ) : workbenchContextMode === "cloud" ? (
                                        <BoardCloud key={project.uid} projectUID={project.uid} panel />
                                    ) : workbenchContextMode === "mcp" && currentUser ? (
                                        <BoardMcpConnection
                                            key={`${currentUser.uid}:${project.uid}`}
                                            projectUID={project.uid}
                                            projectTitle={project.title}
                                            userUID={currentUser.uid}
                                        />
                                    ) : workbenchContextMode === "outline" ? (
                                        <BoardOutlineSidebar
                                            cardUID={activeCard?.uid}
                                            onRelations={() => showWorkbenchContext("relations")}
                                            onNavigate={() => isMobile && setActiveSidePanel(undefined)}
                                        />
                                    ) : (
                                        <BoardWikiSidebar projectUID={project.uid} onNavigate={() => setActiveSidePanel(undefined)} />
                                    )}
                                </Suspense>
                            </div>
                        )}
                    </div>
                }
                workbenchContextHidden={!isContextOpen || isMobile || !!selectCardViewType}
                mobileWorkbenchContext={
                    isMobile && !selectCardViewType && activeSidePanel
                        ? {
                              title: isBotScopeOpened ? t("settings.Bots") : workbenchContextTitle,
                              immersive: !isBotScopeOpened && workbenchContextMode === "chat",
                              icon: isBotScopeOpened
                                  ? "bot"
                                  : {
                                        explorer: "panel-left",
                                        "my-work": "list-checks",
                                        changes: "circle-dot",
                                        inbox: "inbox",
                                        activity: "history",
                                        relations: "network",
                                        outline: "list-tree",
                                        wiki: "notebook-pen",
                                        schedule: "calendar",
                                        cloud: "cloud",
                                        mcp: "plug",
                                        chat: "message-circle",
                                        app: "puzzle",
                                    }[workbenchContextMode],
                              onClose: () => setActiveSidePanel(undefined),
                          }
                        : undefined
                }
                className="!p-0"
            >
                {!isProjectLoading && currentUser && project ? (
                    <Flex
                        position="relative"
                        w="full"
                        h="full"
                        className={cn("h-full min-h-0", pageRoute === "settings" ? "overflow-y-auto overflow-x-hidden" : "overflow-hidden")}
                    >
                        <BoardViewportProvider>
                            <Box className="relative min-w-0 flex-1">
                                <div inert={!!myWorkFilter} className={cn("size-full", myWorkFilter && "pointer-events-none invisible")}>
                                    <Box
                                        className={cn(
                                            "relative size-full",
                                            isCardPage &&
                                                isCardExpanded &&
                                                !selectCardViewType &&
                                                "pointer-events-none absolute inset-0 -z-[9999] overflow-hidden"
                                        )}
                                    >
                                        <Suspense fallback={<SkeletonComponent />}>
                                            <PageComponent key={project.uid} project={project} currentUser={currentUser} />
                                        </Suspense>
                                    </Box>
                                    {isCardPage && (
                                        <SuspenseComponent isPage>
                                            <BoardCardPage
                                                key={pageRoute}
                                                projectUID={project.uid}
                                                cardUID={pageRoute}
                                                embedded
                                                isExpanded={isCardExpanded}
                                                setIsExpanded={setIsCardExpanded}
                                            />
                                        </SuspenseComponent>
                                    )}
                                    {!isCardPage &&
                                        !selectCardViewType &&
                                        (isMobile || boardChat || renderedViewType === "board" || flippedCards.length > 0) && (
                                            <div ref={nowWorkToggleRef}>
                                                <BoardFloatingNavigation
                                                    key={project.uid}
                                                    project={project}
                                                    currentUser={currentUser}
                                                    dockEnabled={renderedViewType === "board"}
                                                    items={[
                                                        {
                                                            key: "now-work",
                                                            label: t("dashboard.Now work"),
                                                            icon: "list-ordered",
                                                            active: nowWorkOpen,
                                                            hidden: isMobile || renderedViewType !== "board",
                                                            onClick: () => setNowWorkOpen((open) => !open),
                                                        },
                                                    ]}
                                                />
                                            </div>
                                        )}
                                </div>
                                {myWorkFilter && (
                                    <div
                                        ref={myWorkWorkspaceRef}
                                        tabIndex={-1}
                                        className="absolute inset-0 overflow-y-auto bg-background"
                                        data-my-work-workspace=""
                                    >
                                        <Suspense fallback={<SkeletonBoard />}>
                                            <MyWorkSidebar
                                                projectUID={project.uid}
                                                filter={myWorkFilter}
                                                onFilterChange={selectMyWorkFilter}
                                                onClear={() => setMyWorkSelection(null)}
                                                onNavigate={() => {
                                                    setMyWorkSelection(null);
                                                    if (isMobile) setActiveSidePanel(undefined);
                                                }}
                                            />
                                        </Suspense>
                                    </div>
                                )}
                            </Box>
                            {nowWorkOpen && !isMobile && !isCardPage && !selectCardViewType && renderedViewType === "board" && (
                                <aside
                                    className="h-full w-80 max-w-[40%] shrink-0 overflow-y-auto border-l bg-background"
                                    data-now-work=""
                                    aria-label={t("dashboard.Now work")}
                                >
                                    <div className="flex justify-end p-2">
                                        <Button
                                            size="icon"
                                            variant="ghost"
                                            aria-label={t("common.Close")}
                                            onClick={() => {
                                                setNowWorkOpen(false);
                                                nowWorkToggleRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
                                            }}
                                        >
                                            <IconComponent icon="x" size="4" />
                                        </Button>
                                    </div>
                                    <Suspense fallback={<SkeletonBoard />}>
                                        <MyWorkSidebar projectUID={project.uid} compact order="priority" />
                                    </Suspense>
                                </aside>
                            )}
                        </BoardViewportProvider>
                    </Flex>
                ) : (
                    <SkeletonComponent />
                )}
            </BoardPaneLayout>
        </>
    );
}

function BoardBotScopeSidebar({ project }: { project: Project.TModel }): React.JSX.Element {
    const currentUserRoleActions = project.useField("current_auth_role_actions");
    const { hasRoleAction } = useRoleActionFilter(currentUserRoleActions);

    return (
        <Box className="h-full overflow-y-auto">
            <Suspense fallback={<Skeleton className="m-3 h-24" />}>
                <BoardAutomationRuns project={project} />
            </Suspense>
            {hasRoleAction(ProjectRole.EAction.Update) && (
                <BoardBotScopeList target={{ target_table: "project", target: project }} className="pb-3" />
            )}
        </Box>
    );
}

export default BoardProxy;
