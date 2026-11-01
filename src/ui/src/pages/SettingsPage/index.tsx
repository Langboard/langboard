import usePinnedAppRail from "@/core/apps/usePinnedAppRail";
import { DashboardStyledLayout } from "@/components/Layout";
import { IActivityRailItem } from "@/components/Layout/ActivityRail";
import useGetSettingRoles from "@/controllers/api/settings/useGetSettingRoles";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { AppSettingProvider } from "@/core/providers/AppSettingProvider";
import { useAuth } from "@/core/providers/AuthProvider";
import { ROUTES } from "@/core/routing/constants";
import { lazy, Suspense, useEffect, useState } from "react";
import Button from "@/components/base/Button";
import { useTranslation } from "react-i18next";
import { useSocket } from "@/core/providers/SocketProvider";
import { EHttpStatus, ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import { IS_OLLAMA_RUNNING } from "@/constants";
import { Navigate, useLocation } from "react-router";

import { AuthUser } from "@/core/models";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ApiKeyRole, McpRole, SettingRole } from "@/core/models/roles";
import useGetOllamaHealth from "@/controllers/api/settings/ollama/useGetOllamaHealth";

import SettingsLoadState from "./SettingsLoadState";

import { settingsRedirect } from "@/pages/SettingsPage/SettingsNavigation";

const MailSettingsPage = lazy(() => import("./MailSettingsPage"));
const BotsPage = lazy(() => import("./BotsPage"));
const ApiComfortToolsPage = lazy(() => import("./ApiComfortToolsPage"));
const GlobalRelationshipsPage = lazy(() => import("./GlobalRelationshipsPage"));
const InternalBotsPage = lazy(() => import("./InternalBotsPage"));
const WebhooksPage = lazy(() => import("./WebhooksPage"));
const NotificationSchedulePage = lazy(() => import("./NotificationSchedulePage"));
const UsersPage = lazy(() => import("./UsersPage"));
const ApiKeysPage = lazy(() => import("./ApiKeysPage"));
const KeywordSearchPage = lazy(() => import("./KeywordSearchPage"));
const OllamaPage = lazy(() => import("./OllamaPage"));
const McpServerPage = lazy(() => import("./McpServerPage"));
const WorkflowStagesPage = lazy(() => import("./WorkflowStagesPage"));
const GlobalLabelsPage = lazy(() => import("./GlobalLabelsPage"));
const ProjectTemplatesPage = lazy(() => import("./ProjectTemplatesPage"));
const AppGroupsPage = lazy(() => import("./AppGroupsPage"));
const AppPackagesPage = lazy(() => import("./AppPackagesPage"));
const AppGovernancePage = lazy(() => import("./AppGovernancePage"));

function SettingsProxy(): React.JSX.Element {
    const { currentUser } = useAuth();
    const [t] = useTranslation();
    const socket = useSocket();
    const navigate = usePageNavigateRef();
    const pathname = useLocation().pathname.split("/").slice(0, 3).join("/");
    const { data, error, isFetching, refetch } = useGetSettingRoles();
    const [isReady, setIsReady] = useState(false);
    const [isOllamaAvailable, setIsOllamaAvailable] = useState(false);
    const [isOllamaHealthChecked, setIsOllamaHealthChecked] = useState(!IS_OLLAMA_RUNNING);
    const { mutateAsync: getOllamaHealthMutateAsync } = useGetOllamaHealth({ interceptToast: false });

    useEffect(() => {
        if (!error) {
            return;
        }

        const { handle } = setupApiErrorHandler({
            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
            },
        });

        handle(error);
    }, [error]);

    useEffect(() => {
        if (!data) {
            setIsReady(() => false);
            return;
        }

        if (currentUser) {
            currentUser.setting_role_actions = data.setting_role_actions ?? [];
            currentUser.api_key_role_actions = data.api_key_role_actions ?? [];
            currentUser.mcp_role_actions = data.mcp_role_actions ?? [];
        }

        const topicIds = Object.values(ESettingSocketTopicID);
        socket.subscribe(ESocketTopic.AppSettings, topicIds, () => {
            setIsReady(() => true);
        });
        setIsReady(() => true);

        return () => {
            socket.unsubscribe(ESocketTopic.AppSettings, topicIds);
        };
    }, [currentUser, data, socket]);

    useEffect(() => {
        if (!IS_OLLAMA_RUNNING) {
            setIsOllamaAvailable(false);
            setIsOllamaHealthChecked(true);
            return;
        }

        let isMounted = true;

        getOllamaHealthMutateAsync({})
            .then((health) => {
                if (!isMounted) {
                    return;
                }

                setIsOllamaAvailable(health.available);
                setIsOllamaHealthChecked(true);
            })
            .catch(() => {
                if (!isMounted) {
                    return;
                }

                setIsOllamaAvailable(false);
                setIsOllamaHealthChecked(true);
            });

        return () => {
            isMounted = false;
        };
    }, []);

    if (pathname === ROUTES.SETTINGS.OLLAMA && !IS_OLLAMA_RUNNING) {
        return <Navigate to={ROUTES.SETTINGS.API_KEYS} replace />;
    }

    return (
        <>
            {isReady && currentUser && (pathname !== ROUTES.SETTINGS.OLLAMA || isOllamaHealthChecked) ? (
                <SettingsProxyDisplay currentUser={currentUser} isOllamaAvailable={isOllamaAvailable} />
            ) : (
                <DashboardStyledLayout headerNavs={[]} headerTitle={t("board.Settings")} activityRailItems={[]} aria-busy={isFetching}>
                    <SettingsLoadState error={Boolean(error)} isFetching={isFetching} retry={() => void refetch()} />
                </DashboardStyledLayout>
            )}
        </>
    );
}

function SettingsProxyDisplay({ currentUser, isOllamaAvailable }: { currentUser: AuthUser.TModel; isOllamaAvailable: bool }): React.JSX.Element {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const pinnedApps = usePinnedAppRail({ userUID: currentUser.uid });
    const pathname = useLocation().pathname.split("/").slice(0, 3).join("/");
    const apiKeyRoleActions = currentUser.useField("api_key_role_actions");
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const mcpRoleActions = currentUser.useField("mcp_role_actions");
    const { hasRoleAction: hasApiKeyRoleAction } = useRoleActionFilter(apiKeyRoleActions);
    const { hasRoleAction: hasSettingRoleAction } = useRoleActionFilter(settingRoleActions);
    const { hasRoleAction: hasMcpRoleAction } = useRoleActionFilter(mcpRoleActions);

    const settingsNavs: Record<string, IActivityRailItem> = {
        [ROUTES.SETTINGS.MAIL]: {
            icon: "mail",
            label: t("settings.mail.title"),
            onClick: () => navigate(ROUTES.SETTINGS.MAIL),
            hidden: !currentUser.is_admin,
        },
        [ROUTES.SETTINGS.KEYWORD_SEARCH]: {
            icon: "search",
            label: t("settings.keywordSearch.title"),
            onClick: () => navigate(ROUTES.SETTINGS.KEYWORD_SEARCH),
            hidden: !currentUser.is_admin,
        },
        [ROUTES.SETTINGS.APP_PACKAGES]: {
            icon: "package",
            label: t("appStore.Manage installation"),
            onClick: () => navigate(ROUTES.SETTINGS.APP_PACKAGES),
            hidden: !currentUser.is_admin,
        },
        [ROUTES.SETTINGS.APPS]: {
            icon: "app-window",
            label: t("settings.Apps"),
            onClick: () => navigate(ROUTES.SETTINGS.APPS),
        },
        [ROUTES.SETTINGS.APP_GROUPS]: {
            icon: "layers",
            label: t("settings.App groups"),
            onClick: () => navigate(ROUTES.SETTINGS.APP_GROUPS),
            hidden: !currentUser.is_admin,
        },
        [ROUTES.SETTINGS.WORKFLOW_STAGES]: {
            icon: "list-tree",
            label: t("settings.Workflow stages"),
            onClick: () => navigate(ROUTES.SETTINGS.WORKFLOW_STAGES),
            hidden: !currentUser.is_admin || !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.WorkflowStage),
        },
        [ROUTES.SETTINGS.GLOBAL_LABELS]: {
            icon: "tags",
            label: t("settings.Global labels"),
            onClick: () => navigate(ROUTES.SETTINGS.GLOBAL_LABELS),
            hidden: !currentUser.is_admin || !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.GlobalLabel),
        },
        [ROUTES.SETTINGS.PROJECT_TEMPLATES]: {
            icon: "layout-template",
            label: t("settings.Project templates"),
            onClick: () => navigate(ROUTES.SETTINGS.PROJECT_TEMPLATES),
            hidden: !currentUser.is_admin,
        },
        [ROUTES.SETTINGS.API_KEYS]: {
            icon: "key-round",
            label: t("settings.API keys"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.API_KEYS);
            },
            hidden: !hasApiKeyRoleAction(...Object.values(ApiKeyRole.EAction)),
        },
        [ROUTES.SETTINGS.USERS]: {
            icon: "users",
            label: t("settings.Users"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.USERS);
            },
            hidden: !currentUser.is_admin || !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.User, SettingRole.EAction.GroupRead),
        },
        [ROUTES.SETTINGS.BOTS]: {
            icon: "bot",
            label: t("settings.Bots"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.BOTS);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.Bot),
        },
        [ROUTES.SETTINGS.INTERNAL_BOTS]: {
            icon: "bot-message-square",
            label: t("settings.Internal bots"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.INTERNAL_BOTS);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.InternalBot),
        },
        [ROUTES.SETTINGS.GLOBAL_RELATIONSHIPS]: {
            icon: "waypoints",
            label: t("settings.Global relationships"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.GLOBAL_RELATIONSHIPS);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.GlobalRelationship),
        },
        [ROUTES.SETTINGS.API_COMFORT_TOOLS]: {
            icon: "package-plus",
            label: t("settings.API comfort tools"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.API_COMFORT_TOOLS);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.ApiComfortTool),
        },
        [ROUTES.SETTINGS.WEBHOOKS]: {
            icon: "webhook",
            label: t("settings.Webhooks"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.WEBHOOKS);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.Webhook),
        },
        [ROUTES.SETTINGS.NOTIFICATION_SCHEDULE]: {
            icon: "bell",
            label: t("settings.Notification schedule"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.NOTIFICATION_SCHEDULE);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.NotificationSchedule),
        },
        [ROUTES.SETTINGS.MCP_TOOL_GROUPS]: {
            icon: "package",
            label: t("mcp.MCP Server"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.MCP_TOOL_GROUPS);
            },
            hidden: !hasMcpRoleAction(...Object.values(McpRole.EAction)),
        },
    };

    if (IS_OLLAMA_RUNNING && isOllamaAvailable) {
        settingsNavs[ROUTES.SETTINGS.OLLAMA] = {
            icon: "ollama",
            label: t("settings.Ollama"),
            onClick: () => {
                navigate(ROUTES.SETTINGS.OLLAMA);
            },
            hidden: !hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.Ollama),
        };
    }

    if (settingsNavs[pathname]) {
        settingsNavs[pathname].active = true;
    }

    const groups = [
        {
            label: "Workspace setup",
            icon: "layout-template",
            paths: [
                ROUTES.SETTINGS.PROJECT_TEMPLATES,
                ROUTES.SETTINGS.WORKFLOW_STAGES,
                ROUTES.SETTINGS.GLOBAL_LABELS,
                ROUTES.SETTINGS.GLOBAL_RELATIONSHIPS,
            ],
        },
        {
            label: "People and notifications",
            icon: "users",
            paths: [ROUTES.SETTINGS.USERS, ROUTES.SETTINGS.NOTIFICATION_SCHEDULE, ROUTES.SETTINGS.MAIL],
        },
        {
            label: "AI and automation",
            icon: "bot",
            paths: [ROUTES.SETTINGS.INTERNAL_BOTS, ROUTES.SETTINGS.BOTS, ROUTES.SETTINGS.OLLAMA, ROUTES.SETTINGS.KEYWORD_SEARCH],
        },
        {
            label: "Apps and integrations",
            icon: "app-window",
            paths: [
                ROUTES.SETTINGS.APPS,
                ROUTES.SETTINGS.APP_GROUPS,
                ROUTES.SETTINGS.APP_PACKAGES,
                ROUTES.SETTINGS.MCP_TOOL_GROUPS,
                ROUTES.SETTINGS.API_KEYS,
                ROUTES.SETTINGS.WEBHOOKS,
                ROUTES.SETTINGS.API_COMFORT_TOOLS,
            ],
        },
    ]
        .map((group) => ({ ...group, paths: group.paths.filter((path) => settingsNavs[path] && !settingsNavs[path].hidden) }))
        .filter((group) => group.paths.length);
    const activeGroup = groups.find((group) => group.paths.some((path) => path === pathname)) ?? groups[0];

    let pageContent;
    switch (pathname) {
        case ROUTES.SETTINGS.MAIL:
            pageContent = <MailSettingsPage currentUser={currentUser} />;
            break;
        case ROUTES.SETTINGS.APP_PACKAGES:
            pageContent = <AppPackagesPage currentUser={currentUser} />;
            break;
        case ROUTES.SETTINGS.APP_GROUPS:
            pageContent = <AppGroupsPage key={currentUser.uid} currentUser={currentUser} />;
            break;
        case ROUTES.SETTINGS.APPS:
            pageContent = <AppGovernancePage currentUser={currentUser} />;
            break;
        case ROUTES.SETTINGS.WORKFLOW_STAGES:
            pageContent = <WorkflowStagesPage currentUser={currentUser} />;
            break;
        case ROUTES.SETTINGS.GLOBAL_LABELS:
            pageContent = <GlobalLabelsPage currentUser={currentUser} />;
            break;
        case ROUTES.SETTINGS.PROJECT_TEMPLATES:
            pageContent = <ProjectTemplatesPage />;
            break;
        case ROUTES.SETTINGS.API_KEYS:
            pageContent = <ApiKeysPage />;
            break;
        case ROUTES.SETTINGS.USERS:
            pageContent = <UsersPage />;
            break;
        case ROUTES.SETTINGS.BOTS:
            pageContent = <BotsPage />;
            break;
        case ROUTES.SETTINGS.INTERNAL_BOTS:
            pageContent = <InternalBotsPage />;
            break;
        case ROUTES.SETTINGS.GLOBAL_RELATIONSHIPS:
            pageContent = <GlobalRelationshipsPage />;
            break;
        case ROUTES.SETTINGS.API_COMFORT_TOOLS:
            pageContent = <ApiComfortToolsPage />;
            break;
        case ROUTES.SETTINGS.WEBHOOKS:
            pageContent = <WebhooksPage />;
            break;
        case ROUTES.SETTINGS.NOTIFICATION_SCHEDULE:
            pageContent = <NotificationSchedulePage />;
            break;
        case ROUTES.SETTINGS.MCP_TOOL_GROUPS:
            pageContent = <McpServerPage />;
            break;
        case ROUTES.SETTINGS.KEYWORD_SEARCH:
            pageContent = currentUser.is_admin ? <KeywordSearchPage /> : null;
            break;
        case ROUTES.SETTINGS.OLLAMA:
            pageContent = IS_OLLAMA_RUNNING && isOllamaAvailable ? <OllamaPage /> : null;
            break;
    }

    useEffect(() => {
        const redirect = settingsRedirect(settingsNavs, pathname, ROUTES.DASHBOARD.PROJECTS.ALL);
        if (redirect) navigate(redirect, { replace: true });
    }, [pathname, currentUser.is_admin, hasApiKeyRoleAction, hasSettingRoleAction, hasMcpRoleAction, isOllamaAvailable]);

    return (
        <DashboardStyledLayout
            headerNavs={[]}
            headerTitle={t("board.Settings")}
            activityRailItems={[
                { icon: "folder-kanban", label: t("dashboard.Projects"), mobile: "primary", onClick: () => navigate(ROUTES.DASHBOARD.PROJECTS.ALL) },
                ...pinnedApps.items,
                { icon: "store", label: t("settings.App store"), mobile: "add", onClick: () => navigate(ROUTES.APPS.ROUTE) },
                ...groups.map((group) => ({
                    icon: group.icon,
                    label: t(`settings.${group.label}`),
                    active: group === activeGroup,
                    placement: "bottom" as const,
                    onClick: () => navigate(group.paths[0]),
                })),
            ]}
        >
            <AppSettingProvider currentUser={currentUser}>
                <nav aria-label={t("board.Settings")} className="flex flex-wrap gap-2 border-b p-4">
                    {activeGroup?.paths.map((path) => (
                        <Button
                            key={path}
                            size="sm"
                            variant={pathname === path ? "secondary" : "ghost"}
                            aria-current={pathname === path ? "page" : undefined}
                            onClick={settingsNavs[path].onClick}
                        >
                            {settingsNavs[path].label}
                        </Button>
                    ))}
                </nav>
                <Suspense
                    fallback={
                        <div role="status" className="p-6 text-sm text-muted-foreground">
                            {t("common.Loading...")}
                        </div>
                    }
                >
                    {pageContent}
                </Suspense>
            </AppSettingProvider>
        </DashboardStyledLayout>
    );
}

export default SettingsProxy;
