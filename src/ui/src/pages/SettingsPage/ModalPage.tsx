import { ROUTES } from "@/core/routing/constants";
import { memo, useEffect, useState } from "react";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { AuthUser, McpToolGroup } from "@/core/models";
import BotCreateFormDialog from "@/pages/SettingsPage/components/bots/BotCreateFormDialog";
import InternalBotCreateFormDialog from "@/pages/SettingsPage/components/internalBots/InternalBotCreateFormDialog";
import ApiKeyCreateFormDialog from "@/pages/SettingsPage/components/apiKeys/ApiKeyCreateFormDialog";
import GlobalRelationshipCreateFormDialog from "@/pages/SettingsPage/components/relationships/GlobalRelationshipCreateFormDialog";
import UserCreateFormDialog from "@/pages/SettingsPage/components/users/UserCreateFormDialog";
import WebhookCreateFormDialog from "@/pages/SettingsPage/components/webhook/WebhookCreateFormDialog";
import McpToolGroupCreateFormDialog from "@/pages/SettingsPage/components/mcpToolGroups/McpToolGroupCreateFormDialog";
import { useAuth } from "@/core/providers/AuthProvider";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ApiKeyRole, McpRole, SettingRole } from "@/core/models/roles";

const ModalPage = () => {
    const { currentUser } = useAuth();

    if (!currentUser) {
        return null;
    }

    return <ModalPageDisplay currentUser={currentUser} />;
};

const ModalPageDisplay = memo(({ currentUser }: { currentUser: AuthUser.TModel }) => {
    const navigate = usePageNavigateRef();
    const [isOpened, setIsOpened] = useState(true);
    const apiKeyRoleActions = currentUser.useField("api_key_role_actions");
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const mcpRoleActions = currentUser.useField("mcp_role_actions");
    const { hasRoleAction: hasApiKeyRoleAction } = useRoleActionFilter(apiKeyRoleActions);
    const { hasRoleAction: hasSettingRoleAction } = useRoleActionFilter(settingRoleActions);
    const { hasRoleAction: hasMcpRoleAction } = useRoleActionFilter(mcpRoleActions);

    const pathname = location.pathname;

    const moveToBack = () => {
        if (pathname.startsWith(ROUTES.SETTINGS.CREATE_MCP_TOOL_GROUP(""))) {
            navigate(ROUTES.SETTINGS.MCP_TOOL_GROUPS);
            return;
        }

        switch (pathname) {
            case ROUTES.SETTINGS.CREATE_API_KEY:
                navigate(ROUTES.SETTINGS.API_KEYS);
                break;
            case ROUTES.SETTINGS.CREATE_USER:
                navigate(ROUTES.SETTINGS.USERS);
                break;
            case ROUTES.SETTINGS.CREATE_BOT:
                navigate(ROUTES.SETTINGS.BOTS);
                break;
            case ROUTES.SETTINGS.CREATE_INTERNAL_BOT:
                navigate(ROUTES.SETTINGS.INTERNAL_BOTS);
                break;
            case ROUTES.SETTINGS.CREATE_GLOBAL_RELATIONSHIP:
                navigate(ROUTES.SETTINGS.GLOBAL_RELATIONSHIPS);
                break;
            case ROUTES.SETTINGS.CREATE_WEBHOOK:
                navigate(ROUTES.SETTINGS.WEBHOOKS);
                break;
            default:
                navigate(ROUTES.SETTINGS.ROUTE);
                break;
        }
    };

    const changeIsOpenedState = (opened: bool) => {
        if (!opened) {
            moveToBack();
        }
        setIsOpened(opened);
    };

    const props = {
        opened: isOpened,
        setOpened: changeIsOpenedState,
        currentUser,
    };

    let modalContent;
    switch (pathname) {
        case ROUTES.SETTINGS.CREATE_API_KEY:
            modalContent = <ApiKeyCreateFormDialog {...props} />;
            break;
        case ROUTES.SETTINGS.CREATE_USER:
            modalContent = <UserCreateFormDialog {...props} />;
            break;
        case ROUTES.SETTINGS.CREATE_BOT:
            modalContent = <BotCreateFormDialog {...props} />;
            break;
        case ROUTES.SETTINGS.CREATE_INTERNAL_BOT:
            modalContent = <InternalBotCreateFormDialog {...props} />;
            break;
        case ROUTES.SETTINGS.CREATE_GLOBAL_RELATIONSHIP:
            modalContent = <GlobalRelationshipCreateFormDialog {...props} />;
            break;
        case ROUTES.SETTINGS.CREATE_WEBHOOK:
            modalContent = <WebhookCreateFormDialog {...props} />;
            break;
        default:
            modalContent = null;
            break;
    }

    if (pathname.startsWith(ROUTES.SETTINGS.CREATE_MCP_TOOL_GROUP(""))) {
        const groupType = pathname.split("/").slice(-1)[0] as McpToolGroup.TGroupType;
        modalContent = <McpToolGroupCreateFormDialog {...props} groupType={groupType} />;
    }

    useEffect(() => {
        switch (pathname) {
            case ROUTES.SETTINGS.API_KEYS:
                if (!hasApiKeyRoleAction(...Object.values(ApiKeyRole.EAction))) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.USERS:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.User)) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.BOTS:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.Bot)) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.INTERNAL_BOTS:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.InternalBot)) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.GLOBAL_RELATIONSHIPS:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.GlobalRelationship)) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.API_COMFORT_TOOLS:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.ApiComfortTool)) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.WEBHOOKS:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.Webhook)) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.MCP_TOOL_GROUPS:
                if (!hasMcpRoleAction(...Object.values(McpRole.EAction))) {
                    moveToBack();
                }
                break;
            case ROUTES.SETTINGS.OLLAMA:
                if (!hasSettingRoleAction(...SettingRole.CATEGORIZED_MAP.Ollama)) {
                    moveToBack();
                }
                break;
        }
    }, [hasApiKeyRoleAction, hasSettingRoleAction, hasMcpRoleAction]);

    return modalContent;
});

export default ModalPage;
