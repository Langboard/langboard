"""Durable archive intent, independent of Inbox and external action outcomes."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppCardArchive(BaseDbModel, table=True):
    # No cascading foreign keys: lifecycle evidence survives native deletion.
    card_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("revision",))
    project_id: int = SnowflakeIDField(nullable=False, index=True)
    actor_id: int = SnowflakeIDField(nullable=False)
    app_key: str = Field(nullable=False)
    card_revision: int = Field(nullable=False, unique_groups=("revision",))
    receipt: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["card_id", "app_key", "card_revision"]
