from abc import abstractmethod
from datetime import datetime
from typing import Any, ClassVar
from ...core.db import SoftDeleteModel
from ...core.types import SafeDateTime


class BaseNotificationScheduleModel(SoftDeleteModel):
    OPERATOR_WITHIN_NEXT_DAYS: ClassVar[str] = "within_next_days"
    OPERATOR_OVERDUE: ClassVar[str] = "overdue"
    OPERATOR_OLDER_THAN_DAYS: ClassVar[str] = "older_than_days"
    OPERATOR_GREATER_THAN_SECONDS: ClassVar[str] = "greater_than_seconds"
    OPERATOR_EQUALS: ClassVar[str] = "equals"
    OPERATOR_IS_EMPTY: ClassVar[str] = "is_empty"
    OPERATOR_IS_NOT_EMPTY: ClassVar[str] = "is_not_empty"

    RECIPIENT_PROJECT_OWNER: ClassVar[str] = "project_owner"
    RECIPIENT_PROJECT_MEMBERS: ClassVar[str] = "project_members"
    RECIPIENT_CARD_ASSIGNEES: ClassVar[str] = "card_assignees"
    RECIPIENT_CHECKITEM_USER: ClassVar[str] = "checkitem_user"

    @classmethod
    @abstractmethod
    def get_notification_schedule_rule_fields(cls) -> list[dict[str, Any]]: ...

    @classmethod
    @abstractmethod
    def get_notification_schedule_rule_recipients(cls) -> list[str]: ...

    @classmethod
    def get_notification_schedule_rule_field_values(cls) -> dict[str, list[Any]]:
        return {}

    @classmethod
    def get_notification_schedule_rule_notification_type(cls, field: str | None, operator: str | None) -> Any:
        return None

    def get_notification_schedule_rule_message_vars(
        self, field: str | None, operator: str | None, now: SafeDateTime
    ) -> dict[str, Any] | None:
        if not field or not hasattr(self, field):
            return {}

        field_value = getattr(self, field)
        if isinstance(field_value, datetime):
            return {field: field_value.isoformat()}

        return {}
