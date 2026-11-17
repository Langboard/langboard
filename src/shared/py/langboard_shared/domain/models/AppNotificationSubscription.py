"""Explicit personal notification consent and immutable outbox admission history."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime


class AppNotificationSubscription(BaseDbModel, table=True):
    user_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("user_request",))
    request_id: str = Field(nullable=False, max_length=128, unique_groups=("user_request",))
    app_key: str = Field(nullable=False)
    connection_id: int = SnowflakeIDField(nullable=False)
    connection_identity: str = Field(nullable=False)
    destination_id: int = SnowflakeIDField(nullable=False)
    destination_revision: int = Field(nullable=False)
    generation: int = Field(default=1, nullable=False)
    state: str = Field(default="enabled", nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["app_key", "generation", "state"]


class AppNotificationDispatch(BaseDbModel, table=True):
    subscription_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("subscription_request",))
    request_id: str = Field(nullable=False, max_length=128, unique_groups=("subscription_request",))
    event_id: int | None = SnowflakeIDField(nullable=True, unique=True)
    expires_at: SafeDateTime = DateTimeField(default=None, nullable=False)
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False, repr=False)
    receipt: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False, repr=False)
    receipt_requests: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False, repr=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["subscription_id", "event_id"]
