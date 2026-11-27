from typing import Any
from ...core.db import ApiField, BaseDbModel, Field


class ScimGroup(BaseDbModel, table=True):
    external_id: str | None = Field(default=None, nullable=True, unique=True, index=True, api_field=ApiField())
    display_name: str = Field(nullable=False, index=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["external_id", "display_name"]
