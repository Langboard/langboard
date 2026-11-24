"""Instance-approved external service declarations, not executable plugin code."""

import hashlib
import json
from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class AppDefinition(BaseDbModel, table=True):
    key: str = Field(nullable=False, unique=True, index=True)
    declaration: dict = Field(sa_type=JSON, nullable=False)
    is_enabled: bool = Field(default=True, nullable=False)
    generation: int = Field(default=1, nullable=False)
    approved_by: SnowflakeID = SnowflakeIDField(foreign_key=User.expr("id"), nullable=False)
    package: dict | None = Field(default=None, sa_type=JSON(none_as_null=True), nullable=True)

    def edit_revision(self) -> str:
        payload = {
            "key": self.key,
            "declaration": self.declaration,
            "is_enabled": self.is_enabled,
            "generation": self.generation,
            "approved_by": int(self.approved_by),
        }
        if self.package is not None:
            payload["package"] = self.package
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def registry_response(self) -> dict:
        return {
            "declaration": self.declaration,
            "is_enabled": self.is_enabled,
            "generation": self.generation,
            "revision": self.edit_revision(),
            "installation": None
            if self.package is None
            else {key: value for key, value in self.package.items() if key != "storage"},
        }

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["key", "generation", "is_enabled"]
