"""Personal filter shortcuts, scoped to one board and account."""

from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Project import Project
from .User import User


class UserBoardFilterPreference(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(
        foreign_key=User, nullable=False, unique_groups=("user_board_filter_preference_scope",)
    )
    project_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Project, nullable=False, unique_groups=("user_board_filter_preference_scope",)
    )
    revision: int = Field(default=1, nullable=False)
    pinned_filters: list[dict] = Field(default_factory=list, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["user_id", "project_id", "revision"]
