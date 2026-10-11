"""One indexed policy row per instance or organization scope."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Organization import Organization


class AppGovernancePolicy(BaseDbModel, table=True):
    scope_key: str = Field(nullable=False, unique=True)
    organization_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Organization, nullable=True, unique=True)
    mode: str | None = Field(default="approved_only", nullable=True)
    generation: int = Field(default=1, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["scope_key", "mode", "generation"]
