"""Committed native workflow effects; evidence survives deletion of its source."""

from sqlalchemy import JSON, CheckConstraint, Index
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class WorkflowEffectRun(BaseDbModel, table=True):
    __table_args__ = (
        CheckConstraint("duration_ms >= 0", name="workflow_effect_duration"),
        CheckConstraint("actor_type IN ('user','bot','profile')", name="workflow_effect_actor"),
        Index("ix_workflow_effect_run_stage_page", "stage_id", "id"),
        Index("ix_workflow_effect_run_execution_key", "execution_key", unique=True),
    )
    # No cascading foreign keys: deleting work must not erase execution evidence.
    stage_id: SnowflakeID = SnowflakeIDField(nullable=False)
    stage_key: str = Field(nullable=False)
    stage_revision: str = Field(nullable=False, max_length=64)
    project_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    card_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    actor_id: SnowflakeID = SnowflakeIDField(nullable=False)
    actor_type: str = Field(nullable=False, max_length=8)
    source: str = Field(nullable=False, max_length=20)
    status: str = Field(default="succeeded", nullable=False, max_length=20)
    effects: list[str] = Field(nullable=False, sa_type=JSON)
    started_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    finished_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    duration_ms: int = Field(nullable=False)
    result: dict[str, int] = Field(nullable=False, sa_type=JSON)
    execution_key: str | None = Field(default=None, nullable=True, max_length=128)
    profile_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    profile_name: str | None = Field(default=None, nullable=True, max_length=255)
    error_code: str | None = Field(default=None, nullable=True, max_length=64)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["stage_key", "source", "card_id"]
