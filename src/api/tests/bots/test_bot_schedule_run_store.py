"""Durable retry boundaries are independent of provider calls and process-local state."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models.BotSchedule import BotScheduleRunningType
from langboard_shared.domain.models.BotScheduleRun import BotScheduleRun
from langboard_shared.tasks.bots.utils.BotScheduleRunStore import (
    claim_schedule_run,
    finish_schedule_run,
    reconcile_expired_schedule_runs,
    reserve_schedule_run,
)
from langboard_shared.tasks.bots.utils.requests.BaseBotRequest import BotRequestOutcome
from sqlalchemy import create_engine, text


@pytest.fixture(params=["sqlite", "postgresql-test"])
def occurrence(monkeypatch, tmp_path, request):
    # File-backed DB retains the occurrence after each native transaction closes.
    schema = None
    if request.param == "postgresql-test":
        database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not database_url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
        schema = "bot_schedule_run_test_" + uuid4().hex
        engine = create_engine(database_url)
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'runs.sqlite'}")
    BotScheduleRun.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    now = SafeDateTime.now()
    schedule = SimpleNamespace(
        id=1, bot_id=2, start_at=now, interval_str="* * * * *", running_type=BotScheduleRunningType.Onetime
    )
    association = SimpleNamespace(id=3, __tablename__="card_bot_schedule")
    try:
        yield schedule, association, now
    finally:
        if schema:
            with engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def row(run_id):
    with DbSession.atomic() as db:
        return db.exec(SqlBuilder.select.table(BotScheduleRun).where(BotScheduleRun.id == run_id)).first()


def test_same_occurrence_reserves_once_and_reschedule_has_new_identity(occurrence):
    schedule, scope, now = occurrence
    first = reserve_schedule_run(schedule, scope, now=now)
    assert reserve_schedule_run(schedule, scope, now=now + timedelta(seconds=1)).id == first.id
    assert row(first.id).attempt == 0
    schedule.start_at += timedelta(minutes=1)
    second = reserve_schedule_run(schedule, scope, now=now)
    assert second.id != first.id and second.occurrence_key != first.occurrence_key


def test_expiry_recovery_drains_bounded_batches_without_touching_accepted_or_live_runs(occurrence):
    schedule, scope, now = occurrence
    expired = []
    for _ in range(3):
        expired.append(reserve_schedule_run(schedule, scope, now=now - timedelta(days=2)))
        schedule.start_at += timedelta(minutes=1)
    accepted = reserve_schedule_run(schedule, scope, now=now - timedelta(days=2))
    lease = claim_schedule_run(accepted.id, now=now - timedelta(days=2))
    assert finish_schedule_run(lease, BotRequestOutcome.Accepted, now=now - timedelta(days=2))
    schedule.start_at += timedelta(minutes=1)
    live = reserve_schedule_run(schedule, scope, now=now)
    reconcile_expired_schedule_runs(now=now, batch_size=2)
    assert [row(item.id).state for item in expired] == ["failed", "failed", "pending"]
    reconcile_expired_schedule_runs(now=now, batch_size=2)
    assert all(row(item.id).state == "failed" for item in expired)
    assert row(accepted.id).state == "accepted" and row(accepted.id).attempt == 1
    assert row(live.id).state == "pending" and row(live.id).attempt == 0


def test_claim_is_exclusive_and_attempt_token_fences_stale_finish(occurrence):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now)
    lease = claim_schedule_run(run.id, now=now)
    assert lease.attempt == 1 and claim_schedule_run(run.id, now=now) is None
    assert not finish_schedule_run(replace(lease, token="wrong"), BotRequestOutcome.Accepted, now=now)
    assert not finish_schedule_run(replace(lease, attempt=2), BotRequestOutcome.Accepted, now=now)
    assert finish_schedule_run(lease, BotRequestOutcome.Accepted, now=now)
    assert row(run.id).state == "accepted"
    assert not finish_schedule_run(lease, BotRequestOutcome.RetryableFailure, now=now)
    assert claim_schedule_run(run.id, now=now + timedelta(hours=1)) is None


def test_backoff_attempt_limit_and_reload_keep_original_occurrence(occurrence):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now, max_attempts=2)
    first = claim_schedule_run(run.id, now=now)
    assert finish_schedule_run(first, BotRequestOutcome.RetryableFailure, now=now)
    saved = row(run.id)
    assert saved.state == "pending" and saved.error_code == "retryable_failure"
    assert claim_schedule_run(run.id, now=now + timedelta(seconds=29)) is None
    later = now + timedelta(seconds=30)
    second = claim_schedule_run(run.id, now=later)
    assert second.attempt == 2 and second.occurrence_key == first.occurrence_key and second.token != first.token
    assert not finish_schedule_run(first, BotRequestOutcome.Accepted, now=later)
    assert finish_schedule_run(second, BotRequestOutcome.RetryableFailure, now=later)
    assert (row(run.id).state, row(run.id).error_code) == ("failed", "retry_limit_exceeded")
    assert claim_schedule_run(run.id, now=later + timedelta(hours=1)) is None


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_retry_window_expiry_is_terminal(occurrence, after_dispatch):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now, retry_window_seconds=20)
    if after_dispatch:
        lease = claim_schedule_run(run.id, now=now)
        assert finish_schedule_run(lease, BotRequestOutcome.RetryableFailure, now=now)
    else:
        assert claim_schedule_run(run.id, now=now + timedelta(seconds=20)) is None
    assert (row(run.id).state, row(run.id).error_code) == ("failed", "retry_window_expired")


def test_expired_dispatch_is_uncertain_not_permission_to_replay(occurrence):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now)
    lease = claim_schedule_run(run.id, now=now, lease_seconds=1)
    later = now + timedelta(seconds=1)
    assert not finish_schedule_run(lease, BotRequestOutcome.Accepted, now=later)
    assert claim_schedule_run(run.id, now=later) is None
    saved = row(run.id)
    assert saved.state == "uncertain" and saved.attempt == 1
    assert saved.lease_token is None and saved.error_code == "dispatch_lease_expired"


@pytest.mark.parametrize(
    "outcome,state", [(BotRequestOutcome.Skipped, "cancelled"), (BotRequestOutcome.PermanentFailure, "failed")]
)
def test_terminal_outcomes_are_not_retried(occurrence, outcome, state):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now)
    lease = claim_schedule_run(run.id, now=now)
    assert finish_schedule_run(lease, outcome, now=now)
    assert row(run.id).state == state
    assert claim_schedule_run(run.id, now=now + timedelta(seconds=300)) is None


def test_unknown_result_and_invalid_limits_cannot_mutate_occurrence(occurrence):
    schedule, scope, now = occurrence
    with pytest.raises(ValueError):
        reserve_schedule_run(schedule, scope, now=now, max_attempts=0)
    run = reserve_schedule_run(schedule, scope, now=now)
    lease = claim_schedule_run(run.id, now=now)
    with pytest.raises(ValueError):
        finish_schedule_run(lease, None, now=now)
    assert row(run.id).state == "dispatching"


def test_two_database_workers_cannot_claim_the_same_attempt(occurrence):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now)
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=5)
        return claim_schedule_run(run.id, now=now)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: worker(), range(2)))
    assert len([result for result in results if result is not None]) == 1
    assert row(run.id).attempt == 1


def test_nested_native_transaction_uses_fresh_attempt_and_rolls_back_together(occurrence):
    schedule, scope, now = occurrence
    run = reserve_schedule_run(schedule, scope, now=now)
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic() as db:
            loaded = db.exec(SqlBuilder.select.table(BotScheduleRun).where(BotScheduleRun.id == run.id)).first()
            assert loaded.attempt == 0
            lease = claim_schedule_run(run.id, now=now)
            assert lease.attempt == 1
            assert finish_schedule_run(lease, BotRequestOutcome.Accepted, now=now)
            raise RuntimeError("rollback")
    assert (row(run.id).state, row(run.id).attempt) == ("pending", 0)
