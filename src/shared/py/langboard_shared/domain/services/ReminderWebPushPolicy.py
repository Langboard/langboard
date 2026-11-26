"""Personal delivery preferences and their local-time quiet window."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy import select, update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models.CardReminderWebPushDelivery import CardReminderWebPushDelivery
from ..models.User import User
from ..models.UserReminderWebPushPreference import UserReminderWebPushPreference
from ..models.UserWebPushSubscription import UserWebPushSubscription
from .WebPushSubscription import WebPushSessionDenied


class QuietHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(min_length=1, max_length=64)
    start_minute: int = Field(strict=True, ge=0, lt=1440)
    end_minute: int = Field(strict=True, ge=0, lt=1440)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unknown timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_minute == self.end_minute:
            raise ValueError("Quiet hours must have different start and end")
        return self


class ReminderWebPushPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quiet_hours: QuietHours | None = None
    block_urgent: StrictBool = False
    show_preview: StrictBool = False


def _wall_boundary(wall: datetime, zone: ZoneInfo, *, end: bool) -> datetime:
    # Keep fall-back hours continuous; move a nonexistent boundary to the next valid minute.
    for _ in range(1441):
        candidates = [wall.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)]
        valid = [instant for instant in candidates if instant.astimezone(zone).replace(tzinfo=None) == wall]
        if valid:
            return max(valid) if end else min(valid)
        wall += timedelta(minutes=1)
    raise ValueError("Quiet hours could not be resolved")


def quiet_hours_end(hours: QuietHours, now: datetime) -> datetime | None:
    zone = ZoneInfo(hours.timezone)
    day = now.astimezone(zone).date()
    overnight = hours.start_minute > hours.end_minute
    for date in (day - timedelta(days=1), day) if overnight else (day,):
        midnight = datetime.combine(date, time())
        start = _wall_boundary(midnight + timedelta(minutes=hours.start_minute), zone, end=False)
        end = _wall_boundary(midnight + timedelta(days=overnight, minutes=hours.end_minute), zone, end=True)
        if start <= now < end:
            return end
    return None


def _row(db: DbSession, user_id: int) -> UserReminderWebPushPreference | None:
    return db.exec(
        SqlBuilder.select.table(UserReminderWebPushPreference).where(UserReminderWebPushPreference.user_id == user_id)
    ).first()


def get_reminder_web_push_policy(db: DbSession, user_id: int) -> ReminderWebPushPolicy:
    row = _row(db, user_id)
    if row is None:
        return ReminderWebPushPolicy()
    return ReminderWebPushPolicy(
        quiet_hours=row.quiet_hours,
        block_urgent=row.block_urgent,
        show_preview=row.show_preview,
    )


def set_reminder_web_push_policy(user: User, policy: ReminderWebPushPolicy) -> None:
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(User).where(User.id == user.id).with_for_update()).first()
        if (
            not current
            or current.deleted_at is not None
            or current.activated_at is None
            or current.auth_version != user.auth_version
        ):
            raise WebPushSessionDenied()
        row = _row(db, current.id)
        values = policy.model_dump()
        if row is None:
            db.insert(UserReminderWebPushPreference(user_id=current.id, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
            db.update(row)

        db.exec(
            update(CardReminderWebPushDelivery)
            .where(
                CardReminderWebPushDelivery.state == "pending",
                CardReminderWebPushDelivery.last_result == "quiet_hours",
                CardReminderWebPushDelivery.subscription_id.in_(
                    select(UserWebPushSubscription.id).where(UserWebPushSubscription.user_id == current.id)
                ),
            )
            .values(next_attempt_at=SafeDateTime.now())
        )
