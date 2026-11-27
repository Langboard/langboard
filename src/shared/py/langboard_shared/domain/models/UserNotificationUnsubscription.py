from enum import Enum
from typing import Any, ClassVar
from ...core.db import BaseDbModel, EnumLikeType, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User
from .UserNotification import NotificationType


class NotificationChannel(Enum):
    Web = "web"
    Email = "email"
    Mobile = "mobile"
    IoT = "iot"


class NotificationScope(Enum):
    All = "all"
    Specific = "specific"


class UserNotificationUnsubscription(BaseDbModel, table=True):
    UNAVAILABLE_TYPES: ClassVar[list[NotificationType]] = [NotificationType.ProjectInvited]
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    channel: NotificationChannel = Field(nullable=False, sa_type=EnumLikeType(NotificationChannel))
    notification_type: NotificationType = Field(nullable=False, sa_type=EnumLikeType(NotificationType))
    scope_type: NotificationScope = Field(nullable=False, sa_type=EnumLikeType(NotificationScope))
    specific_table: str | None = Field(nullable=True)
    specific_id: SnowflakeID | None = SnowflakeIDField(nullable=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return []
