from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class UserProfile(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, unique=True, nullable=False)
    industry: str = Field(nullable=False)
    purpose: str = Field(nullable=False)
    affiliation: str | None = Field(default=None, nullable=True)
    position: str | None = Field(default=None, nullable=True)

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return {
            "industry": "string",
            "purpose": "string",
            "affiliation": "string",
            "position": "string",
            **(schema or {}),
        }

    def api_response(self) -> dict[str, Any]:
        return {
            "industry": self.industry,
            "purpose": self.purpose,
            "affiliation": self.affiliation,
            "position": self.position,
        }

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["user_id", "industry", "purpose", "affiliation", "position"]
