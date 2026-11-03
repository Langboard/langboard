"""One unique action claim; transport acceptance never implies success."""
from sqlalchemy import JSON, Index
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class WorkflowActionRun(BaseDbModel, table=True):
    __table_args__ = (Index("ix_workflow_action_run_identity", "idempotency_key", unique=True),)
    rule_run_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    action_id: SnowflakeID = SnowflakeIDField(nullable=False)
    kind: str = Field(nullable=False, max_length=32)
    executor_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    executor_type: str = Field(nullable=False, max_length=16)
    idempotency_key: str = Field(nullable=False, max_length=64)
    status: str = Field(default="queued", nullable=False, max_length=20)
    attempt: int = Field(default=0, nullable=False)
    attempts: list = Field(default_factory=list, nullable=False, sa_type=JSON)
    result: dict = Field(default_factory=dict, nullable=False, sa_type=JSON)
    error_code: str | None = Field(default=None, nullable=True, max_length=64)
    started_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    finished_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    legacy_effect_run_id: SnowflakeID | None = SnowflakeIDField(nullable=True)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["rule_run_id", "action_id", "status"]
