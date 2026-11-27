import ApiKeyRole from "@/models/ApiKeyRole";
import Bot from "@/models/Bot";
import Card from "@/models/Card";
import ChatHistory from "@/models/ChatHistory";
import ChatSession from "@/models/ChatSession";
import GraphApprovalRequest from "@/models/GraphApprovalRequest";
import BotScheduleGraphApprovalRequest from "@/models/BotScheduleGraphApprovalRequest";
import BotTriggerGraphApprovalRequest from "@/models/BotTriggerGraphApprovalRequest";
import ChatGraphApprovalRequest from "@/models/ChatGraphApprovalRequest";
import EditorGraphApprovalRequest from "@/models/EditorGraphApprovalRequest";
import ManualScopeRunGraphApprovalRequest from "@/models/ManualScopeRunGraphApprovalRequest";
import InternalBot from "@/models/InternalBot";
import McpRole from "@/models/McpRole";
import ProjectAssignedInternalBot from "@/models/ProjectAssignedInternalBot";
import ProjectAssignedUser from "@/models/ProjectAssignedUser";
import ProjectChatSession from "@/models/ProjectChatSession";
import ProjectRole from "@/models/ProjectRole";
import ProjectWiki from "@/models/ProjectWiki";
import ProjectWikiAssignedUser from "@/models/ProjectWikiAssignedUser";
import SettingRole from "@/models/SettingRole";
import User from "@/models/User";
import UserNotification from "@/models/UserNotification";
import UserNotificationUnsubscription from "@/models/UserNotificationUnsubscription";

export const ALL_ENTITIES = [
    ApiKeyRole,
    Bot,
    Card,
    ChatHistory,
    ChatSession,
    GraphApprovalRequest,
    ChatGraphApprovalRequest,
    EditorGraphApprovalRequest,
    BotTriggerGraphApprovalRequest,
    BotScheduleGraphApprovalRequest,
    ManualScopeRunGraphApprovalRequest,
    InternalBot,
    McpRole,
    ProjectAssignedUser,
    ProjectAssignedInternalBot,
    ProjectChatSession,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    SettingRole,
    User,
    UserNotification,
    UserNotificationUnsubscription,
];
