"""Administrator-owned presentation grouping; never an execution identity."""

import hashlib
import json
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class AppComposition(BaseDbModel, table=True):
    key: str = Field(nullable=False, unique=True, index=True)
    name: str = Field(nullable=False)
    description: str = Field(nullable=False)
    generation: int = Field(default=1, nullable=False)
    approved_by: SnowflakeID = SnowflakeIDField(foreign_key=User.expr("id"), nullable=False)

    def response(self, module_keys: list[str]) -> dict:
        item = {"key": self.key, "name": self.name, "description": self.description,
                "generation": self.generation, "module_keys": module_keys}
        payload = {**item, "approved_by": int(self.approved_by)}
        item["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return item

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["key", "generation"]


class AppCompositionMember(BaseDbModel, table=True):
    composition_id: SnowflakeID = SnowflakeIDField(foreign_key=AppComposition.expr("id"), nullable=False, index=True)
    # Built-in modules have no AppDefinition row; their stable key is intentional.
    module_key: str = Field(nullable=False, unique=True, index=True)
    order: int = Field(nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["module_key", "composition_id"]
