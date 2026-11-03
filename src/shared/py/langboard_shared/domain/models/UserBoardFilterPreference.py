"""Personal filter shortcuts, scoped to one board and account."""

from sqlalchemy import JSON, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Project import Project
from .User import User


class UserBoardFilterPreference(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="user_board_filter_preference_scope"),)
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False)
    revision: int = Field(default=1, nullable=False)
    pinned_filters: list[dict] = Field(default_factory=list, sa_type=JSON, nullable=False)

    saved_views: list[dict] = Field(default_factory=list, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["user_id", "project_id", "revision"]
