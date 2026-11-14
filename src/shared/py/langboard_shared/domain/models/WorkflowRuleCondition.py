from typing import Any
from sqlalchemy import JSON, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


class WorkflowRuleCondition(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("rule_id", "order", name="uq_workflow_rule_condition_order"),)
    rule_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    order: int = Field(nullable=False)
    kind: str = Field(nullable=False, max_length=64)
    operator: str = Field(nullable=False, max_length=8)
    value: Any = Field(nullable=False, sa_type=JSON)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["rule_id", "kind"]
