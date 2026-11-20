from sqlalchemy import JSON, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


class WorkflowRuleAction(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("rule_id", "order", name="uq_workflow_rule_action_order"),)
    rule_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    order: int = Field(nullable=False)
    kind: str = Field(nullable=False, max_length=32)
    executor_kind: str = Field(nullable=False, max_length=16)
    executor_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    config: dict = Field(default_factory=dict, nullable=False, sa_type=JSON)
    requires_previous_success: bool = Field(default=False, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["rule_id", "kind", "order"]
