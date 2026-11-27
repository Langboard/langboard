from ...core.db import ApiField, BaseDbModel, EnumLikeType, Field, SnowflakeIDField
from ...core.routing import ApiErrorCode
from ...core.types import SafeDateTime, SnowflakeID
from .Bot import Bot
from .McpToolGroup import McpToolGroup
from .User import User


class McpToolGroupUsage(BaseDbModel, table=True):
    tool_group_id: SnowflakeID = SnowflakeIDField(foreign_key=McpToolGroup, nullable=False)
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True)
    bot_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Bot, nullable=True)
    execution_time_ms: float = Field(default=0.0, nullable=False)
    is_success: bool = Field(default=True, nullable=False)
    error_code: ApiErrorCode | None = Field(default=None, nullable=True, sa_type=EnumLikeType(ApiErrorCode))
    executed_at: SafeDateTime = Field(default_factory=SafeDateTime.now, api_field=ApiField())

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["tool_group_id", "user_id", "bot_id", "is_success", "executed_at"]
