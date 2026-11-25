import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest


path = Path(__file__).resolve().parents[3] / "shared/py/langboard_shared/domain/services/CardReminderSchedule.py"
spec = importlib.util.spec_from_file_location("card_reminder_schedule", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ReminderSchedule, next_reminder = module.ReminderSchedule, module.next_reminder

UTC = timezone.utc


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_daily_dst_gap_and_fold():
    gap = ReminderSchedule("America/New_York", anchor=dt("2024-03-09T07:30:00"), frequency="daily")
    assert next_reminder(gap, dt("2024-03-09T07:30:00")) == dt("2024-03-11T06:30:00")
    fold = ReminderSchedule("America/New_York", anchor=dt("2024-11-02T05:30:00"), frequency="daily")
    assert next_reminder(fold, dt("2024-11-02T05:30:00")) == dt("2024-11-03T05:30:00")


def test_monthly_skip_last_day_nth_and_workday():
    anchor = dt("2024-01-31T09:00:00")
    assert next_reminder(ReminderSchedule("UTC", anchor, frequency="monthly"), anchor) == dt("2024-03-31T09:00:00")
    assert next_reminder(ReminderSchedule("UTC", anchor, frequency="monthly", missing_day="last"), anchor) == dt(
        "2024-02-29T09:00:00"
    )
    assert next_reminder(ReminderSchedule("UTC", anchor, frequency="monthly", nth_weekday=(2, 0)), anchor) == dt(
        "2024-02-12T09:00:00"
    )
    assert next_reminder(ReminderSchedule("UTC", anchor, frequency="monthly", last_workday=True), anchor) == dt(
        "2024-02-29T09:00:00"
    )


def test_biweekly_multiple_days_and_bounds():
    anchor = dt("2024-01-01T09:00:00")
    rule = ReminderSchedule("UTC", anchor, frequency="weekly", interval=2, weekdays=(0, 2), count=3)
    assert next_reminder(rule, anchor) == dt("2024-01-03T09:00:00")
    assert next_reminder(rule, dt("2024-01-03T09:00:00")) == dt("2024-01-15T09:00:00")
    assert next_reminder(rule, dt("2024-01-15T09:00:00"), consumed_count=3) is None
    assert next_reminder(rule, dt("2024-03-10T00:00:00"), consumed_count=1) == dt("2024-03-11T09:00:00")
    assert next_reminder(ReminderSchedule("UTC", anchor, frequency="daily", until=anchor), anchor) is None


def test_deadline_relative_and_validation():
    rule = ReminderSchedule("Asia/Seoul", deadline_offset=timedelta(hours=-2))
    assert next_reminder(rule, dt("2024-01-01T00:00:00"), deadline=dt("2024-01-01T05:00:00")) == dt(
        "2024-01-01T03:00:00"
    )
    assert next_reminder(rule, dt("2024-01-01T00:00:00")) is None
    with pytest.raises(ValueError):
        next_reminder(rule, datetime(2024, 1, 1))
