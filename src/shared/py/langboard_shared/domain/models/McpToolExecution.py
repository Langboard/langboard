"""Native execution metadata only. No arguments, results, or error text."""

from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .Bot import Bot
from .Card import Card
from .Project import Project
from .ProjectColumn import ProjectColumn
from .User import User


class McpToolExecution(BaseDbModel, table=True):
    request_id: str = Field(nullable=False, unique=True, max_length=32)
    tool: str = Field(nullable=False, max_length=128)
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True, index=True)
    bot_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Bot, nullable=True, index=True)
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    card_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Card, nullable=True, index=True)
    project_column_id: SnowflakeID | None = SnowflakeIDField(foreign_key=ProjectColumn, nullable=True)
    outcome: str = Field(nullable=False, max_length=20)
    started_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    finished_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    duration_ms: float = Field(nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["request_id", "tool", "outcome"]
