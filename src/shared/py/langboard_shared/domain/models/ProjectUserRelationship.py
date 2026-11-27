from typing import Any
from ...core.db import BaseDbModel, DateTimeField, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .Project import Project
from .User import User


class ProjectUserRelationship(BaseDbModel, table=True):
    project_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Project, nullable=False, index=True, unique_groups=("project_user_related_user",)
    )
    user_id: SnowflakeID = SnowflakeIDField(
        foreign_key=User, nullable=False, index=True, unique_groups=("project_user_related_user",)
    )
    related_user_id: SnowflakeID = SnowflakeIDField(
        foreign_key=User, nullable=False, index=True, unique_groups=("project_user_related_user",)
    )
    last_related_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False, onupdate=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["project_id", "user_id", "related_user_id", "last_related_at"]
