from enum import Enum
from typing import Any, ClassVar
from ...core.db import ApiField, EnumLikeType, Field, SoftDeleteModel


class BotPlatform(Enum):
    Default = "default"
    Langflow = "langflow"
    N8N = "n8n"


class BotPlatformRunningType(Enum):
    Default = "default"
    Endpoint = "endpoint"


class BaseBotModel(SoftDeleteModel):
    AVAILABLE_RUNNING_TYPES_BY_PLATFORM: ClassVar[dict[BotPlatform, list[BotPlatformRunningType]]] = {
        BotPlatform.Default: [BotPlatformRunningType.Default],
        BotPlatform.Langflow: [BotPlatformRunningType.Endpoint],
        BotPlatform.N8N: [BotPlatformRunningType.Default],
    }
    ALLOWED_ALL_IPS_BY_PLATFORMS: ClassVar[dict[BotPlatform, list[BotPlatformRunningType]]] = {
        BotPlatform.Default: [BotPlatformRunningType.Default],
        BotPlatform.Langflow: [],
        BotPlatform.N8N: [BotPlatformRunningType.Default],
    }
    platform: BotPlatform = Field(
        nullable=False,
        sa_type=EnumLikeType(BotPlatform),
        api_field=ApiField(by_conditions={"is_setting": ("both", True)}),
    )
    platform_running_type: BotPlatformRunningType = Field(
        nullable=False,
        sa_type=EnumLikeType(BotPlatformRunningType),
        api_field=ApiField(by_conditions={"is_setting": ("both", True)}),
    )

    def notification_data(self) -> dict[str, Any]:
        return self.api_response()
