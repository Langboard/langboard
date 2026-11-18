"""Explicit user consent for an app-attested notification destination.

External identities and routing references are private. They are not host user
credentials, board grants or permission to call user-authenticated APIs.
"""

from typing import Any
from sqlalchemy import JSON, text
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime


class AppNotificationDestinationChallenge(BaseDbModel, table=True):
    user_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("user_request",))
    connection_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("user_request",))
    request_id: str = Field(nullable=False, max_length=128, unique_groups=("user_request",))
    app_key: str = Field(nullable=False)
    kind: str = Field(default="personal", nullable=False, sa_column_kwargs={"server_default": text("'personal'")})
    connection_identity: str = Field(nullable=False, max_length=64)
    token_hash: str = Field(nullable=False, max_length=64, repr=False)
    expires_at: SafeDateTime = DateTimeField(default=None, nullable=False)
    state: str = Field(default="pending", nullable=False)
    revision: int = Field(default=1, nullable=False)
    proof_id: int | None = SnowflakeIDField(nullable=True, unique=True)
    proof_credential_id: int | None = SnowflakeIDField(nullable=True)
    proof_request_id: str | None = Field(default=None, nullable=True, max_length=128)
    proof: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False, repr=False)
    destination_id: int | None = SnowflakeIDField(nullable=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["app_key", "state", "revision"]


class AppNotificationDestination(BaseDbModel, table=True):
    user_id: int = SnowflakeIDField(nullable=False, index=True)
    connection_id: int = SnowflakeIDField(nullable=False, index=True)
    app_key: str = Field(nullable=False)
    # Personal targets are connection-scoped; shared targets are app/issuer/room-scoped.
    # An approval cannot transfer an existing target to another native user.
    target_fingerprint: str = Field(nullable=False, max_length=64, unique=True)
    connection_identity: str = Field(nullable=False, max_length=64)
    challenge_id: int = SnowflakeIDField(nullable=False)
    proof: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False, repr=False)
    state: str = Field(default="verified", nullable=False)
    revision: int = Field(default=1, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["app_key", "state", "revision"]
