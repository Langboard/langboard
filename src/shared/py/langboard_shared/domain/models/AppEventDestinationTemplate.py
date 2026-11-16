"""One administrator-approved service target; personal connections reuse its secret reference."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppEventDestinationTemplate(BaseDbModel, table=True):
    app_key: str = Field(nullable=False, unique=True)
    webhook_id: int = SnowflakeIDField(nullable=False)
    target_revision: str = Field(nullable=False)
    trust_revision: str = Field(nullable=False)
    revision: int = Field(default=1, nullable=False)
    is_enabled: bool = Field(default=True, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["app_key", "revision", "is_enabled"]
