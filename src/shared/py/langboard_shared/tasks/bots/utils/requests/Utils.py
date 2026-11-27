from typing import Any
from .....core.db import BaseDbModel
from .....domain.models import Bot, Project
from .....domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from .....Env import Env
from .BaseBotRequest import BaseBotRequest
from .DefaultRequest import DefaultRequest
from .LangflowRequest import LangflowRequest
from .N8NRequest import N8NRequest


_REQUEST_MAP: dict[BotPlatform, dict[BotPlatformRunningType, tuple[type[BaseBotRequest], str | None]]] = {
    BotPlatform.Default: {
        BotPlatformRunningType.Default: (DefaultRequest, Env.DEFAULT_GRAPH_URL),
    },
    BotPlatform.Langflow: {
        BotPlatformRunningType.Endpoint: (LangflowRequest, None),
    },
    BotPlatform.N8N: {
        BotPlatformRunningType.Default: (N8NRequest, None),
    },
}


def create_request(
    bot: Bot,
    event: str,
    data: dict[str, Any],
    project: Project | None,
    scope_model: BaseDbModel | None,
) -> BaseBotRequest | None:
    request = _REQUEST_MAP.get(bot.platform, {}).get(bot.platform_running_type)
    if not request:
        return None

    request_class, base_url = request
    if base_url is None:
        base_url = bot.api_url
    return request_class(bot, base_url, event, data, project, scope_model)
