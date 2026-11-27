from enum import Enum
from typing import Any
from ...core.db import ApiField, BaseDbModel, CSVType, DateTimeField, EnumLikeType, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .User import User


class ApiKeyProvider(Enum):
    OpenBao = "openbao"
    Hashicorp = "hashicorp"
    Aws = "aws"
    Azure = "azure"


class ApiKeySetting(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(
        foreign_key=User, nullable=False, index=True, api_field=ApiField(name="user_uid")
    )
    name: str = Field(nullable=False, api_field=ApiField())
    value: str = Field(nullable=False)
    provider: ApiKeyProvider = Field(nullable=False, sa_type=EnumLikeType(ApiKeyProvider))
    ip_whitelist: list[str] = Field(default=[], sa_type=CSVType(str), api_field=ApiField())
    activated_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())
    expires_in_days: int | None = Field(default=None, nullable=True, api_field=ApiField())
    expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return SafeDateTime.now() > self.expires_at

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return []
