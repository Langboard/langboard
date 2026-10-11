from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from .AppResourceBinding import AppResourceBinding


class AppSignal(BaseDbModel, table=True):
    """Append-only provider evidence, independent of workflow and reviewer acceptance."""

    resource_id: int = SnowflakeIDField(foreign_key=AppResourceBinding, nullable=False, unique_groups=("resource_event",))
    provider: str = Field(nullable=False)
    event_id: str = Field(nullable=False, unique_groups=("resource_event",))
    event_type: str = Field(nullable=False)
    occurred_at: str = Field(nullable=False)
    external_id: str = Field(nullable=False)
    outcome: str = Field(nullable=False)
    commit_sha: str = Field(nullable=False)
    payload_digest: str = Field(nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["resource_id", "provider", "event_type", "external_id"]
