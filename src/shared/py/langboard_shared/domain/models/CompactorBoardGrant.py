"""Explicit board authority for a dedicated compactor, independent of its provider."""

from sqlalchemy import CheckConstraint, Index
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


class CompactorBoardGrant(BaseDbModel, table=True):
    __table_args__ = (
        Index("ix_compactor_board_grant_identity", "project_id", "internal_bot_id", unique=True),
        CheckConstraint("can_read OR (NOT can_compact AND NOT can_archive)", name="compactor_grant_requires_read"),
    )
    project_id: SnowflakeID = SnowflakeIDField(nullable=False)
    internal_bot_id: SnowflakeID = SnowflakeIDField(nullable=False)
    granted_by_user_id: SnowflakeID = SnowflakeIDField(nullable=False)
    can_read: bool = Field(default=False, nullable=False)
    can_compact: bool = Field(default=False, nullable=False)
    can_archive: bool = Field(default=False, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["project_id", "internal_bot_id"]
