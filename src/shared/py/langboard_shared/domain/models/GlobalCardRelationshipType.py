from typing import Any
from ...core.db import ApiField, BaseDbModel, Field


class GlobalCardRelationshipType(BaseDbModel, table=True):
    parent_name: str = Field(nullable=False, api_field=ApiField())
    child_name: str = Field(nullable=False, api_field=ApiField())
    description: str = Field(default="", nullable=False, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["parent_name", "child_name"]
