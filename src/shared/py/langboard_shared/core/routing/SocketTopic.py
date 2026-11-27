from enum import Enum


GLOBAL_TOPIC_ID = "all"
NONE_TOPIC_ID = "none"


class SocketTopic(Enum):
    Dashboard = "dashboard"
    Board = "board"
    BoardCard = "board_card"
    BoardWiki = "board_wiki"
    BoardWikiPrivate = "board_wiki_private"
    BoardSettings = "board_settings"
    User = "user"
    UserPrivate = "user_private"
    AppSettings = "app_settings"
    OllamaManager = "ollama_manager"
    Global = "global"
    NoneTopic = "none"


class SettingSocketTopicID(Enum):
    # Api Key role management
    ApiKey = "api_key"

    # Setting role management
    User = "user"
    Bot = "bot"
    InternalBot = "internal_bot"
    GlobalRelationship = "global_relationship"
    Webhook = "webhook"
    NotificationSchedule = "notification_schedule"
    Ollama = "ollama"
    ApiComfortTool = "api_comfort_tool"

    # MCP Server management
    McpToolGroup = "mcp_tool_group"
