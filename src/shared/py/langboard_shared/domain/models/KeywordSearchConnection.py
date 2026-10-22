"""Admin-approved, destination-bound native keyword workload grants."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


class KeywordSearchConnection(BaseDbModel, table=True):
    endpoint: str = Field(nullable=False)
    index: str = Field(nullable=False)
    secret_ref: str = Field(nullable=False)
    secret_revision: int = Field(nullable=False)
    active: bool = Field(default=True, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["id"]


class KeywordSearchConfiguration(BaseDbModel, table=True):
    singleton: str = Field(default="instance", unique=True, nullable=False)
    connection_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    reads_enabled: bool = Field(default=False, nullable=False)
    indexing_enabled: bool = Field(default=False, nullable=False)
    revision: int = Field(default=0, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["id"]
