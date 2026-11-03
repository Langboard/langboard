"""One committed native stamp, never a reconstruction of historical activity."""

from typing import Any
from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Card import Card


class CardChangeReceipt(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("card_id", "change_seq", name="uq_card_change_receipt_cursor"),
        CheckConstraint("change_seq > 0", name="ck_card_change_receipt_positive"),
    )

    card_id: SnowflakeID = SnowflakeIDField(foreign_key=Card, nullable=False)
    change_seq: int = Field(nullable=False, sa_type=BigInteger)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "change_seq"]
