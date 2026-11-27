from typing import Any
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .Project import Project
from .User import User


class ProjectAssignedUser(BaseDbModel, table=True):
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    starred: bool = Field(default=False, nullable=False)
    last_viewed_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False, onupdate=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["project_id", "user_id", "starred", "last_viewed_at"]
