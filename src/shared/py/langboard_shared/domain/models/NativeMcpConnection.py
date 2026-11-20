"""Incoming OAuth client connections, independent of outbound app connections."""

from sqlalchemy import UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from .User import User


class NativeMcpConnection(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "client_id"),)
    user_id: int = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    client_id: str = Field(nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["user_id", "client_id"]
