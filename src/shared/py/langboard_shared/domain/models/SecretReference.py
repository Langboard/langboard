"""Stable logical reference; vault locators never belong in public projections."""

from typing import Any
from sqlalchemy import TEXT
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class SecretReference(BaseDbModel, table=True):
    scope: str = Field(nullable=False)
    scope_id: SnowflakeID = SnowflakeIDField(nullable=False)
    name: str = Field(nullable=False)
    creator_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    provider: str = Field(nullable=False, exclude=True)
    locator: str = Field(nullable=False, sa_type=TEXT, exclude=True, repr=False)
    protection_locator: str | None = Field(default=None, nullable=True, sa_type=TEXT, exclude=True, repr=False)
    state: str = Field(default="active", nullable=False)
    revision: int = Field(default=0, nullable=False)

    def metadata(self) -> dict:
        return {
            "uri": f"secret://ref/{self.get_uid()}",
            "name": self.name,
            "scope": self.scope,
            "scope_uid": self._scope_uid(),
            "state": self.state,
            "revision": self.revision,
        }

    def _scope_uid(self) -> str:
        from ...helpers import InfraHelper

        return InfraHelper.convert_uid(self.scope_id)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["scope", "scope_id", "name", "state"]
