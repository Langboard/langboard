"""Card-local optional stage approval binding; immutable rounds retain their audit."""

from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Card import Card
from .DecisionCase import DecisionCase


class CardDecisionGate(BaseDbModel, table=True):
    card_id: SnowflakeID = SnowflakeIDField(foreign_key=Card, nullable=False, unique_groups=("target",))
    target_stage: str = Field(nullable=False, unique_groups=("target",))
    decision_id: SnowflakeID = SnowflakeIDField(foreign_key=DecisionCase, nullable=False)
    round_id: str = Field(nullable=False)
    target_revision: str = Field(nullable=False)
    policy_revision: str = Field(nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["card_id", "target_stage", "round_id"]
