"""One authoritative workflow trigger; child rows own conditions and actions."""
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


class WorkflowRule(BaseDbModel, table=True):
    stage_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    configured_by_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    name: str = Field(nullable=False, max_length=100)
    is_enabled: bool = Field(default=True, nullable=False)
    revision: int = Field(default=1, nullable=False)
    order: int = Field(default=0, nullable=False)
    trigger_kind: str = Field(nullable=False, max_length=20)
    event_type: str | None = Field(default=None, nullable=True, max_length=80)
    interval_seconds: int | None = Field(default=None, nullable=True)
    condition_match: str = Field(default="all", nullable=False, max_length=3)
    retired: bool = Field(default=False, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["stage_id", "name", "revision"]
