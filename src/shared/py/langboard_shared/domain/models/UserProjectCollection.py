"""Personal board groupings; references never grant board access."""

from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class UserProjectCollection(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, unique=True, nullable=False)
    revision: int = Field(default=1, nullable=False)
    collections: list[dict] = Field(default_factory=list, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["user_id", "revision"]
