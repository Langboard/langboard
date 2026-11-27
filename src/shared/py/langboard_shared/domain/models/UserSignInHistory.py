from enum import Enum
from typing import Any
from sqlalchemy import TEXT
from ...core.db import ApiField, BaseDbModel, EnumLikeType, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .User import User


class SignInErrorCode(str, Enum):
    UserNotFound = "user_not_found"
    EmailNotVerified = "email_not_verified"
    InvalidPassword = "invalid_password"
    AccountNotActivated = "account_not_activated"


class UserSignInHistory(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    is_success: bool = Field(default=True, nullable=False)
    ip_address: str | None = Field(default=None, sa_type=TEXT)
    error_code: SignInErrorCode | None = Field(default=None, nullable=True, sa_type=EnumLikeType(SignInErrorCode))
    signed_in_at: SafeDateTime = Field(default_factory=SafeDateTime.now, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["user_id"]
