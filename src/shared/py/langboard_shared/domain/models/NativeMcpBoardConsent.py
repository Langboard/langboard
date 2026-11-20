"""Human approval of one incoming native client on one board."""

from sqlalchemy import JSON, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from .NativeMcpConnection import NativeMcpConnection
from .Project import Project


class NativeMcpBoardConsent(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("connection_id", "project_id"),)
    connection_id: int = SnowflakeIDField(foreign_key=NativeMcpConnection, nullable=False, index=True)
    project_id: int = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    scopes: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)
    revision: int = Field(default=1, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["connection_id", "project_id", "revision"]
