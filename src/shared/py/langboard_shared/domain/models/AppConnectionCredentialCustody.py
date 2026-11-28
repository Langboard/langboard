"""Metadata-only consent and receipt for server-to-server credential custody."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppConnectionCredentialCustody(BaseDbModel, table=True):
    connection_id: int = SnowflakeIDField(nullable=False, unique=True)
    approved_by: int = SnowflakeIDField(nullable=False)
    identity_hash: str = Field(nullable=False)
    destination_id: int = SnowflakeIDField(nullable=False)
    destination_revision: int = Field(nullable=False)
    state: str = Field(default="enabled", nullable=False)
    revision: int = Field(default=1, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["connection_id", "state", "revision"]


class AppConnectionCredentialHandoff(BaseDbModel, table=True):
    custody_id: int = SnowflakeIDField(nullable=False, unique_groups=("custody_request",))
    request_id: str = Field(nullable=False, max_length=128, unique_groups=("custody_request",))
    custody_revision: int = Field(nullable=False)
    credential_id: int = SnowflakeIDField(nullable=False, unique=True)
    state: str = Field(default="prepared", nullable=False)
    renewal_request_id: str | None = Field(default=None, nullable=True, max_length=128)
    notification_challenge_id: int | None = SnowflakeIDField(nullable=True, unique=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["custody_id", "state"]
