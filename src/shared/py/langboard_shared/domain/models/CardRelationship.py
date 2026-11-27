from typing import Any
from ...core.db import ApiField, BaseDbModel, SnowflakeIDField
from ...core.types import SnowflakeID
from .Card import Card
from .GlobalCardRelationshipType import GlobalCardRelationshipType


class CardRelationship(BaseDbModel, table=True):
    relationship_type_id: SnowflakeID = SnowflakeIDField(
        foreign_key=GlobalCardRelationshipType,
        nullable=False,
        index=True,
        api_field=ApiField(name="relationship_type_uid"),
    )
    card_id_parent: SnowflakeID = SnowflakeIDField(
        foreign_key=Card, nullable=False, index=True, api_field=ApiField(name="parent_card_uid")
    )
    card_id_child: SnowflakeID = SnowflakeIDField(
        foreign_key=Card, nullable=False, index=True, api_field=ApiField(name="child_card_uid")
    )

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["relationship_type_id", "card_id_parent", "card_id_child"]
