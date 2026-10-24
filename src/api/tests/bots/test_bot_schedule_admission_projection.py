"""Authorized schedule lists expose bounded admission evidence, not execution success."""

from datetime import timedelta, timezone
import pytest
from langboard_shared.ai import BotScheduleHelper
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models.BotSchedule import BotSchedule, BotScheduleRunningType
from langboard_shared.domain.models.BotScheduleRun import BotScheduleRun
from langboard_shared.domain.models.ProjectColumn import ProjectColumn
from langboard_shared.domain.models.ProjectColumnBotSchedule import ProjectColumnBotSchedule
from sqlalchemy import event, insert
from test_bot_schedule_recovery import scheduled  # noqa: F401


def read_schedules(bot):
    return BotScheduleHelper.get_all_by_scope(ProjectColumnBotSchedule, bot, (ProjectColumn, 4), as_api=True)


def add_run(schedule, association, *, run_id, state, scope_table=None, scope_id=None):
    row = BotScheduleRun(
        id=run_id,
        schedule_id=schedule.id,
        scope_table=scope_table or association.__tablename__,
        scope_id=association.id if scope_id is None else scope_id,
        occurrence_key=f"occurrence-{run_id}",
        state=state,
        attempt=2,
        max_attempts=6,
        next_attempt_at=schedule.start_at + timedelta(seconds=30),
        expires_at=schedule.start_at + timedelta(days=1),
        error_code="retryable_failure" if state == "pending" else None,
    )
    with DbEngine.get_main_engine().begin() as connection:
        connection.execute(
            insert(row.__table__).values(**{c.name: getattr(row, c.name) for c in row.__table__.columns})
        )
    return row


@pytest.mark.parametrize("state", ["pending", "failed", "uncertain", "accepted"])
def test_latest_scoped_admission_is_explicit_and_secret_free(scheduled, state):  # noqa: F811
    bot, schedule, association = scheduled
    add_run(schedule, association, run_id=100, state="failed")
    latest = add_run(schedule, association, run_id=101, state=state)
    # A newer row for another scope or association must never replace this receipt.
    add_run(schedule, association, run_id=102, state="accepted", scope_table="card_bot_schedule")
    add_run(schedule, association, run_id=103, state="accepted", scope_id=999)
    rows = read_schedules(bot)
    assert len(rows) == 1
    admission = rows[0]["last_admission"]
    # SQLite stores UTC timestamps without an offset; compare the same instant.
    for key in ("next_attempt_at", "expires_at"):
        value = admission[key]
        if value.tzinfo is None:
            admission[key] = value.replace(tzinfo=timezone.utc)
    assert admission == {
        "uid": latest.get_uid(),
        "state": state,
        "attempt": 2,
        "max_attempts": 6,
        "next_attempt_at": latest.next_attempt_at,
        "expires_at": latest.expires_at,
        "error_code": latest.error_code,
    }
    assert "completed" not in rows[0]["last_admission"]


def test_multiple_schedules_batch_admissions_in_one_extra_query(scheduled):  # noqa: F811
    bot, schedule, association = scheduled
    add_run(schedule, association, run_id=100, state="failed")
    second = BotSchedule(**{**schedule.model_dump(), "id": 7})
    second_scope = ProjectColumnBotSchedule(id=8, bot_schedule_id=second.id, project_column_id=4)
    with DbEngine.get_main_engine().begin() as connection:
        for row in (second, second_scope):
            connection.execute(
                insert(row.__table__).values(**{c.name: getattr(row, c.name) for c in row.__table__.columns})
            )
    add_run(second, second_scope, run_id=101, state="uncertain")
    queries = []
    engine = DbEngine.get_main_engine()

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        rows = read_schedules(bot)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(rows) == 2
    assert len(queries) == 2
    assert {row["last_admission"]["state"] for row in rows} == {"failed", "uncertain"}
    # Raw scheduler reads retain their original tuple contract.
    raw = BotScheduleHelper.get_all_by_scope(ProjectColumnBotSchedule, bot, (ProjectColumn, 4), as_api=False)
    assert len(raw) == 2 and all(len(pair) == 2 for pair in raw)


def test_unsent_and_recurring_schedules_keep_original_response(scheduled):  # noqa: F811
    bot, schedule, association = scheduled
    assert "last_admission" not in read_schedules(bot)[0]
    add_run(schedule, association, run_id=100, state="failed")
    with DbSession.atomic() as db:
        schedule.running_type = BotScheduleRunningType.Infinite
        db.update(schedule)
    assert "last_admission" not in read_schedules(bot)[0]


def test_server_registers_one_minute_recovery_job_without_duplicate(monkeypatch):
    from crontab import CronTab
    from langboard import ServerRunner

    cron = CronTab(tab="")
    utils = BotScheduleHelper.utils
    monkeypatch.setattr(utils, "get_cron", lambda: cron)
    monkeypatch.setattr(utils, "save_cron", lambda _cron: None)
    monkeypatch.setattr(utils, "reload_cron", lambda: None)
    monkeypatch.setattr(ServerRunner.FastAPIRunner, "run", lambda *args: None)
    ServerRunner.run()
    ServerRunner.run()
    jobs = list(cron.find_comment("bot-schedule-recovery"))
    assert len(jobs) == 1
    assert jobs[0].command == "/app/scripts/run_bot_schedule_recovery.sh"
    assert str(jobs[0].slices) == "* * * * *"
