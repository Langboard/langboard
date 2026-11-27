from typing import Any
from sqlalchemy import JSON
from ...core.db import ApiField, BaseDbModel, CSVType, DateTimeField, Field
from ...core.types import SafeDateTime


class NotificationScheduleRule(BaseDbModel, table=True):
    name: str = Field(nullable=False, api_field=ApiField())
    is_enabled: bool = Field(default=False, nullable=False, index=True, api_field=ApiField())
    interval_str: str = Field(default="0 9 * * *", nullable=False, api_field=ApiField())
    target: str = Field(nullable=False, index=True, api_field=ApiField())
    field: str = Field(nullable=False, index=True, api_field=ApiField())
    operator: str = Field(nullable=False, index=True, api_field=ApiField())
    value: Any | None = Field(default=None, nullable=True, sa_type=JSON, api_field=ApiField())
    recipients: list[str] = Field(default_factory=list, nullable=False, sa_type=CSVType(str), api_field=ApiField())
    repeat_after_hours: int = Field(default=24, nullable=False, api_field=ApiField())
    display_order: int = Field(default=0, nullable=False)
    last_run_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["name", "target", "field", "operator"]
