from typing import Any
from ...core.db import BaseDbModel, SnowflakeIDField
from ...core.types import SnowflakeID
from .ScimGroup import ScimGroup
from .User import User


class ScimGroupMember(BaseDbModel, table=True):
    group_id: SnowflakeID = SnowflakeIDField(
        foreign_key=ScimGroup,
        nullable=False,
        index=True,
        unique_groups=("group_user",),
    )
    user_id: SnowflakeID = SnowflakeIDField(
        foreign_key=User,
        nullable=False,
        index=True,
        unique_groups=("group_user",),
    )

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["group_id", "user_id"]
