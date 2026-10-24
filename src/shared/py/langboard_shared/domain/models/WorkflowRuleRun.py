"""Durable event admission and bounded recovery queue; contains references, never source text."""
from sqlalchemy import JSON, Index, UniqueConstraint
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class WorkflowRuleRun(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("rule_id", "event_id", "residency", name="uq_workflow_rule_event"),
        Index("ix_workflow_rule_run_due", "status", "next_attempt_at"),
    )
    rule_id: SnowflakeID = SnowflakeIDField(nullable=False)
    configured_by_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    rule_revision: int = Field(nullable=False)
    stage_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    stage_revision: str = Field(nullable=False, max_length=64)
    event_id: str = Field(nullable=False, max_length=128)
    event_type: str = Field(nullable=False, max_length=80)
    occurred_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    project_id: SnowflakeID = SnowflakeIDField(nullable=False)
    card_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    residency: str = Field(nullable=False, max_length=40)
    actor_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    actor_type: str = Field(default="system", nullable=False, max_length=8)
    cause_run_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    rule_snapshot: dict = Field(default_factory=dict, nullable=False, sa_type=JSON)
    event_context: dict = Field(default_factory=dict, nullable=False, sa_type=JSON)
    condition_results: list = Field(default_factory=list, nullable=False, sa_type=JSON)
    status: str = Field(default="queued", nullable=False, max_length=20)
    next_attempt_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    finished_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    error_code: str | None = Field(default=None, nullable=True, max_length=64)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["rule_id", "event_id", "status"]
