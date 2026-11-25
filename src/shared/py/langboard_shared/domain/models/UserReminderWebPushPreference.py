"""Personal browser reminder delivery policy shared by a user's own devices."""

from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class UserReminderWebPushPreference(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, unique=True)
    quiet_hours: dict | None = Field(default=None, sa_type=JSON)
    block_urgent: bool = Field(default=False, nullable=False)
    show_preview: bool = Field(default=False, nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["user_id"]
