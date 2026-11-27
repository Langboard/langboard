from enum import Enum
from typing import Any
from ...core.db import ApiField, DateTimeField, EnumLikeType, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .BaseNotificationScheduleModel import BaseNotificationScheduleModel
from .Card import Card
from .Checklist import Checklist
from .User import User


class CheckitemStatus(Enum):
    Started = "started"
    Paused = "paused"
    Stopped = "stopped"


class Checkitem(BaseNotificationScheduleModel, table=True):
    checklist_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Checklist, nullable=False, index=True, api_field=ApiField(name="checklist_uid")
    )
    cardified_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Card, nullable=True)
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True, index=True)
    title: str = Field(nullable=False, api_field=ApiField())
    status: CheckitemStatus = Field(
        default=CheckitemStatus.Stopped,
        nullable=False,
        sa_type=EnumLikeType(CheckitemStatus),
        api_field=ApiField(),
    )
    order: int = Field(default=0, nullable=False, api_field=ApiField())
    accumulated_seconds: int = Field(default=0, nullable=False, api_field=ApiField())
    is_checked: bool = Field(default=False, nullable=False, api_field=ApiField())
    deadline_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {
            "uid": self.get_uid(),
            "title": self.title,
            "status": self.status.value,
            "is_checked": self.is_checked,
            "deadline_at": self.deadline_at.isoformat() if self.deadline_at else None,
        }

    @classmethod
    def get_notification_schedule_rule_fields(cls) -> list[dict[str, Any]]:
        return [
            {"key": "status", "operators": [cls.OPERATOR_EQUALS]},
            {"key": "is_checked", "operators": [cls.OPERATOR_EQUALS]},
            {"key": "deadline_at", "operators": [cls.OPERATOR_WITHIN_NEXT_DAYS, cls.OPERATOR_OVERDUE]},
            {"key": "created_at", "operators": [cls.OPERATOR_OLDER_THAN_DAYS]},
            {"key": "accumulated_seconds", "operators": [cls.OPERATOR_GREATER_THAN_SECONDS]},
        ]

    @classmethod
    def get_notification_schedule_rule_recipients(cls) -> list[str]:
        return [
            cls.RECIPIENT_CHECKITEM_USER,
            cls.RECIPIENT_CARD_ASSIGNEES,
            cls.RECIPIENT_PROJECT_OWNER,
            cls.RECIPIENT_PROJECT_MEMBERS,
        ]

    @classmethod
    def get_notification_schedule_rule_field_values(cls) -> dict[str, list[Any]]:
        return {
            "status": [status.value for status in CheckitemStatus],
            "is_checked": [True, False],
        }

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return [
            "checklist_id",
            "cardified_id",
            "user_id",
            "title",
            "status",
            "order",
            "accumulated_seconds",
            "is_checked",
            "deadline_at",
        ]
