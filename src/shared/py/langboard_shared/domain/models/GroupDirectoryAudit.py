"""Private directory audit, retained independently of group and user lifecycle."""

from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field


class GroupDirectoryAudit(BaseDbModel, table=True):
    # UIDs are immutable facts, deliberately not cascading foreign keys.
    actor_uid: str = Field(nullable=False)
    group_uid: str = Field(nullable=False, index=True)
    action: str = Field(nullable=False)
    revision: int = Field(nullable=False)
    change_facts: dict = Field(default={}, sa_type=JSON)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["group_uid", "action", "revision"]
