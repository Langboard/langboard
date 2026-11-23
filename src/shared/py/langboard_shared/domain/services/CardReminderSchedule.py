"""Pure calendar calculation for card reminders; no persistence or delivery."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReminderSchedule:
    timezone: str
    anchor: datetime | None = None
    deadline_offset: timedelta | None = None
    frequency: Literal["once", "daily", "weekly", "monthly"] = "once"
    interval: int = 1
    weekdays: tuple[int, ...] = ()  # Monday is 0
    month_day: int | None = None
    missing_day: Literal["skip", "last"] = "skip"
    nth_weekday: tuple[int, int] | None = None  # ordinal 1..5 or -1, weekday 0..6
    last_workday: bool = False  # Monday through Friday, no holiday calendar
    until: datetime | None = None  # inclusive instant
    count: int | None = None


def next_reminder(
    schedule: ReminderSchedule,
    after: datetime,
    *,
    deadline: datetime | None = None,
    consumed_count: int = 0,
) -> datetime | None:
    """Return the next UTC instant strictly after *after*.

    Nonexistent DST wall times are skipped; ambiguous times use the first fold.
    Count uses the persisted number of occurrences consumed while the rule was active.
    """

    def aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None

    if not aware(after) or (schedule.until is not None and not aware(schedule.until)):
        raise ValueError("after and until must be timezone-aware")
    if (schedule.anchor is None) == (schedule.deadline_offset is None):
        raise ValueError("exactly one anchor or deadline offset is required")
    anchor = schedule.anchor
    if schedule.deadline_offset is not None:
        if deadline is None:
            return None
        if not aware(deadline):
            raise ValueError("deadline must be timezone-aware")
        anchor = deadline.astimezone(timezone.utc) + schedule.deadline_offset
    if anchor is None or not aware(anchor):
        raise ValueError("anchor must be timezone-aware")
    if schedule.frequency not in ("once", "daily", "weekly", "monthly") or schedule.interval < 1:
        raise ValueError("invalid frequency or interval")
    if consumed_count < 0 or (schedule.count is not None and schedule.count < 1):
        raise ValueError("invalid occurrence count")
    if schedule.count is not None and consumed_count >= schedule.count:
        return None
    if schedule.missing_day not in ("skip", "last") or any(day not in range(7) for day in schedule.weekdays):
        raise ValueError("invalid calendar option")
    if schedule.month_day is not None and not 1 <= schedule.month_day <= 31:
        raise ValueError("invalid month day")
    if schedule.nth_weekday is not None and (
        schedule.nth_weekday[0] not in (-1, 1, 2, 3, 4, 5) or schedule.nth_weekday[1] not in range(7)
    ):
        raise ValueError("invalid nth weekday")
    if sum((schedule.month_day is not None, schedule.nth_weekday is not None, schedule.last_workday)) > 1:
        raise ValueError("monthly selectors are mutually exclusive")
    zone = ZoneInfo(schedule.timezone)
    local_anchor = anchor.astimezone(zone)
    anchor_day = local_anchor.date()
    wall = local_anchor.timetz().replace(tzinfo=None)
    after_utc = after.astimezone(timezone.utc)
    anchor_utc = anchor.astimezone(timezone.utc)
    until = schedule.until.astimezone(timezone.utc) if schedule.until else None
    if until is not None and after_utc >= until:
        return None
    day = max(anchor_day, after.astimezone(zone).date())
    # Gregorian dates repeat after 400 years; longer scans require explicit pagination.
    limit = min(date.max, date(min(day.year + 400, 9999), 12, 31))
    while day <= limit:
        days = (day - anchor_day).days
        months = (day.year - anchor_day.year) * 12 + day.month - anchor_day.month
        match = False
        if schedule.frequency == "once":
            match = days == 0
        elif schedule.frequency == "daily":
            match = days % schedule.interval == 0
        elif schedule.frequency == "weekly":
            week = (days + anchor_day.weekday()) // 7
            match = week % schedule.interval == 0 and day.weekday() in (schedule.weekdays or (anchor_day.weekday(),))
        else:
            last = monthrange(day.year, day.month)[1]
            target = schedule.month_day or anchor_day.day
            if schedule.last_workday:
                target = last
                while date(day.year, day.month, target).weekday() >= 5:
                    target -= 1
            elif schedule.nth_weekday:
                ordinal, weekday = schedule.nth_weekday
                if ordinal == -1:
                    target = last - (date(day.year, day.month, last).weekday() - weekday) % 7
                else:
                    target = 1 + (weekday - date(day.year, day.month, 1).weekday()) % 7 + 7 * (ordinal - 1)
            elif target > last and schedule.missing_day == "last":
                target = last
            match = months % schedule.interval == 0 and day.day == target
        if match:
            local = datetime.combine(day, wall, tzinfo=zone).replace(fold=0)
            instant = anchor_utc if day == anchor_day else local.astimezone(timezone.utc)
            # Round-trip rejects nonexistent local times.
            if instant.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None) and instant >= anchor_utc:
                if until is not None and instant > until:
                    return None
                if instant > after_utc:
                    return instant
        if schedule.frequency == "once" or day == date.max:
            return None
        day += timedelta(days=1)
    raise ValueError("schedule search exceeds 400-year limit")
