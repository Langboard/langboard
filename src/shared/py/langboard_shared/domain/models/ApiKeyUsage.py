from typing import Any
from sqlalchemy import TEXT
from ...core.db import ApiField, BaseDbModel, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .ApiKeySetting import ApiKeySetting


class ApiKeyUsage(BaseDbModel, table=True):
    api_key_id: SnowflakeID | None = SnowflakeIDField(foreign_key=ApiKeySetting, nullable=True, index=True)
    endpoint: str = Field(nullable=False, sa_type=TEXT)
    method: str = Field(default="GET", nullable=False)
    status_code: int = Field(default=200, nullable=False)
    is_success: bool = Field(default=True, nullable=False)
    ip_address: str | None = Field(default=None, sa_type=TEXT)
    used_at: SafeDateTime = Field(default_factory=SafeDateTime.now, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return []
