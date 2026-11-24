"""Connection-owner approval, independent of board MCP consent."""

from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from .AppConnection import AppConnection


class AppMcpConsent(BaseDbModel, table=True):
    connection_id: int = SnowflakeIDField(foreign_key=AppConnection, nullable=False, unique=True)
    identity_hash: str = Field(nullable=False)
    scope_tools: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)
    revision: int = Field(default=1, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["connection_id", "revision"]
