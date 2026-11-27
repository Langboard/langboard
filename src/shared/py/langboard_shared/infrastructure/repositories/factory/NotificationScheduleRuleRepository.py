from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....domain.models import NotificationScheduleRule


class NotificationScheduleRuleRepository(BaseRepository[NotificationScheduleRule]):
    @staticmethod
    def model_cls():
        return NotificationScheduleRule

    @staticmethod
    def name() -> str:
        return "notification_schedule_rule"

    def get_all(self) -> list[NotificationScheduleRule]:
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                SqlBuilder.select.table(NotificationScheduleRule).order_by(
                    NotificationScheduleRule.column("display_order").asc()
                )
            )
            return result.all()

    def get_enabled(self) -> list[NotificationScheduleRule]:
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                SqlBuilder.select.table(NotificationScheduleRule)
                .where(NotificationScheduleRule.column("is_enabled") == True)  # noqa: E712
                .order_by(NotificationScheduleRule.column("display_order").asc())
            )
            return result.all()
