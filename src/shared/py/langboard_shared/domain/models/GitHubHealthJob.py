from datetime import datetime
from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .GitHubLifecycleReceipt import GitHubLifecycleReceipt


class GitHubHealthJob(BaseDbModel, table=True):
    """Durable receipt cursor; no credentials, raw event payload or caller authority."""

    receipt_id: SnowflakeID = SnowflakeIDField(foreign_key=GitHubLifecycleReceipt, nullable=False)
    state: str = Field(default="pending", nullable=False)
    board_after: int = Field(default=0, nullable=False)
    project_id: int | None = Field(default=None, nullable=True)
    resource_after: str | None = Field(default=None, nullable=True)
    blocked_boards: int = Field(default=0, nullable=False)
    attempts: int = Field(default=0, nullable=False)
    available_at: datetime = Field(nullable=False)
    lease_token: str | None = Field(default=None, nullable=True)
    last_error: str | None = Field(default=None, nullable=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["receipt_id", "state", "attempts"]
