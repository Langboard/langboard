"""Exercise durable scheduler dispatch with real storage and native HTTP transport."""

import importlib
import os
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx
import pytest
from crontab import CronTab
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Project, ProjectColumn, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.BotLog import BotLog
from langboard_shared.domain.models.BotSchedule import BotSchedule, BotScheduleRunningType, BotScheduleStatus
from langboard_shared.domain.models.BotScheduleRun import BotScheduleRun
from langboard_shared.domain.models.CardBotSchedule import CardBotSchedule
from langboard_shared.domain.models.ProjectBotSchedule import ProjectBotSchedule
from langboard_shared.domain.models.ProjectColumnBotLog import ProjectColumnBotLog
from langboard_shared.domain.models.ProjectColumnBotSchedule import ProjectColumnBotSchedule
from langboard_shared.tasks.bots.utils.BotScheduleRunStore import get_schedule_run
from sqlalchemy import create_engine, insert, text


scheduler = importlib.import_module("langboard_shared.tasks.bots.BotScheduleTask")
transport_module = importlib.import_module("langboard_shared.tasks.bots.utils.requests.BaseBotRequest")


@pytest.fixture(params=["sqlite", "postgresql"])
def scheduled(monkeypatch, tmp_path, request):
    schema = None
    if request.param == "postgresql":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Disposable PostgreSQL URL not configured")
        schema = "schedule_recovery_" + uuid4().hex
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.sqlite'}")
    for model in (
        User,
        Bot,
        Project,
        ProjectColumn,
        BotSchedule,
        ProjectColumnBotSchedule,
        CardBotSchedule,
        ProjectBotSchedule,
        BotScheduleRun,
        BotLog,
        ProjectColumnBotLog,
    ):
        # Referenced identity tables are outside the dispatch fixture.
        if engine.dialect.name == "postgresql":
            from sqlalchemy.schema import CreateTable

            with engine.begin() as connection:
                connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
                for index in model.__table__.indexes:
                    index.create(connection)
        else:
            model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    bot = Bot(
        id=2,
        name="scheduled",
        bot_uname="scheduled",
        app_api_token="test",
        platform=BotPlatform.Default,
        platform_running_type=BotPlatformRunningType.Default,
    )
    project = Project(id=3, title="Project", owner_id=1)
    column = ProjectColumn(id=4, project_id=project.id, name="Column", order=1)
    schedule = BotSchedule(
        id=5,
        bot_id=bot.id,
        status=BotScheduleStatus.Started,
        running_type=BotScheduleRunningType.Onetime,
        interval_str="* * * * *",
        start_at=SafeDateTime.now() - timedelta(minutes=1),
    )
    association = ProjectColumnBotSchedule(id=6, bot_schedule_id=schedule.id, project_column_id=column.id)
    with engine.begin() as connection:
        for model in (bot, project, column, schedule, association):
            connection.execute(
                insert(model.__table__).values(**{c.name: getattr(model, c.name) for c in model.__table__.columns})
            )
    monkeypatch.setattr(transport_module.BaseBotRequest, "_get_bot_request_headers", lambda self: {})
    from langboard_shared.tasks.webhooks import WebhookTask

    monkeypatch.setattr(WebhookTask, "webhook_task", lambda *args: None)
    monkeypatch.setattr(scheduler.ProjectBotPublisher, "rescheduled", lambda *args: None)
    monkeypatch.setattr(scheduler.ProjectBotPublisher, "log_created", lambda *args: None)
    monkeypatch.setattr(scheduler.ProjectBotPublisher, "log_stack_added", lambda *args: None)
    monkeypatch.setattr(scheduler.BotScheduleHelper.utils, "get_cron", lambda: CronTab(tab=""))
    monkeypatch.setattr(scheduler.BotScheduleHelper.utils, "save_cron", lambda cron: None)
    yield bot, schedule, association
    if schema:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def runs():
    with DbSession.atomic() as db:
        return db.exec(SqlBuilder.select.table(BotScheduleRun)).all()


@pytest.mark.asyncio
async def test_rejected_onetime_keeps_durable_retry_after_scheduler_reload(scheduled, monkeypatch):
    bot, schedule, association = scheduled
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, json={"status": "accepted"})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(bot, schedule, association)
    with DbSession.atomic() as db:
        persisted = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule.id)).first()
        assert persisted.status == BotScheduleStatus.Started
    run = runs()[0]
    assert run.state == "pending" and run.attempt == 1
    with DbSession.atomic() as db:
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    # Recreate the module to discard scheduler-local state before the next cron tick.
    importlib.reload(scheduler)
    await scheduler._check_bot_schedule_runnable(schedule.interval_str)
    saved = get_schedule_run(run.id)
    assert saved.state == "accepted" and saved.attempt == 2 and len(requests) == 2
    import json

    assert (
        json.loads(requests[0].content)["schedule_execution"]["occurrence_key"]
        == json.loads(requests[1].content)["schedule_execution"]["occurrence_key"]
    )


@pytest.mark.asyncio
async def test_scheduled_timeout_does_not_replay_transport(scheduled, monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("ambiguous acceptance", request=request)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(*scheduled)
    await scheduler._run_scheduler(*scheduled)
    assert len(requests) == 1
    assert runs()[0].state == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,state", [(400, "failed"), (429, "pending"), (503, "pending"), (500, "uncertain")])
async def test_explicit_scheduled_outcomes_keep_terminal_receipts(scheduled, monkeypatch, status, state):
    async def handler(request):
        return httpx.Response(status)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(*scheduled)
    run = runs()[0]
    assert run.state == state
    if state in {"failed", "uncertain"}:
        with DbSession.atomic() as db:
            logs = db.exec(SqlBuilder.select.table(BotLog)).all()
        assert any(run.occurrence_key in str(log.message_stack) for log in logs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform,running_type",
    [
        (BotPlatform.N8N, BotPlatformRunningType.Default),
        (BotPlatform.Langflow, BotPlatformRunningType.Endpoint),
    ],
)
async def test_external_failure_has_inspectable_uncertainty_and_no_replay(
    scheduled, monkeypatch, platform, running_type
):
    bot, schedule, association = scheduled
    with DbSession.atomic() as db:
        bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == bot.id)).first()
        bot.platform = platform
        bot.platform_running_type = running_type
        bot.api_url = "https://external.invalid"
        db.update(bot)
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(bot, schedule, association)
    await scheduler._run_scheduler(bot, schedule, association)
    assert runs()[0].state == "uncertain" and len(calls) == 1
    assert b"schedule_execution" not in calls[0].content


@pytest.mark.asyncio
async def test_stale_broker_schedule_does_not_dispatch_new_occurrence(scheduled, monkeypatch):
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == scheduled[1].id)).first()
        current.start_at += timedelta(days=1)
        db.update(current)
    await scheduler._run_scheduler(*scheduled)
    assert runs() == []


@pytest.fixture
def card_scheduled(scheduled):
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import Card
    from langboard_shared.domain.models.CardBotLog import CardBotLog
    from sqlalchemy.schema import CreateTable

    bot, schedule, association = scheduled
    engine = DbEngine.get_main_engine()
    for model in (Card, CardBotLog):
        with engine.begin() as connection:
            connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
    card = Card(id=7, created_by_user_id=1, title="Shared card", project_id=3, project_column_id=4, visibility="SHARED")
    card_association = CardBotSchedule(id=8, card_id=card.id, bot_schedule_id=schedule.id)
    with engine.begin() as connection:
        for model in (card, card_association):
            connection.execute(
                insert(model.__table__).values(**{c.name: getattr(model, c.name) for c in model.__table__.columns})
            )
    return bot, schedule, card_association


@pytest.mark.asyncio
async def test_visibility_revocation_between_attempts_cancels_retry(card_scheduled, monkeypatch):
    from langboard_shared.domain.models import Card

    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(*card_scheduled)
    run = runs()[0]
    with DbSession.atomic() as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == 7)).first()
        card.visibility = "PRIVATE"
        card.owner_user_id = 1
        db.update(card)
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    await scheduler._run_scheduler(*card_scheduled)
    assert runs()[0].state == "cancelled" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["max", "window"])
async def test_scheduler_terminal_retry_limits_are_logged_once(scheduled, monkeypatch, limit):
    from langboard_shared.tasks.bots.utils.BotScheduleRunStore import reserve_schedule_run

    bot, schedule, association = scheduled
    run = reserve_schedule_run(schedule, association, now=SafeDateTime.now(), max_attempts=1)
    if limit == "window":
        with DbSession.atomic() as db:
            run.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(run)
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(*scheduled)
    await scheduler._run_scheduler(*scheduled)
    saved = runs()[0]
    assert saved.state == "failed"
    assert saved.error_code == ("retry_limit_exceeded" if limit == "max" else "retry_window_expired")
    assert len(calls) == (1 if limit == "max" else 0)
    with DbSession.atomic() as db:
        logs = db.exec(SqlBuilder.select.table(BotLog)).all()
    assert sum(saved.occurrence_key in str(log.message_stack) for log in logs) == 1


@pytest.mark.asyncio
async def test_recovery_does_not_admit_first_occurrence_ahead_of_original_cron(scheduled, monkeypatch):
    bot, schedule, association = scheduled
    with DbSession.atomic() as db:
        schedule = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule.id)).first()
        schedule.interval_str = "0 9 * * *"
        db.update(schedule)
    # start_at is in the past, but its original daily cron has not created an occurrence.
    await scheduler.run_scheduled_bots_cron("recover")
    assert runs() == []


@pytest.mark.asyncio
async def test_original_cron_persists_first_occurrence_with_started_status(scheduled, monkeypatch):
    bot, schedule, association = scheduled
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule.id)).first()
        current.status = BotScheduleStatus.Pending
        db.update(current)
    # Simulate a process crash after the original cron transaction, before transport.
    monkeypatch.setattr(scheduler, "_run_scheduler", AsyncMock(side_effect=RuntimeError("process stopped")))
    with pytest.raises(RuntimeError, match="process stopped"):
        await scheduler._check_bot_schedule_runnable(schedule.interval_str)
    assert len(runs()) == 1 and runs()[0].state == "pending" and runs()[0].attempt == 0
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule.id)).first()
    assert saved.status == BotScheduleStatus.Started


@pytest.mark.asyncio
async def test_internal_admission_metadata_never_reaches_schedule_webhooks(scheduled, monkeypatch):
    from langboard_shared.tasks.webhooks import WebhookTask

    sent = []
    monkeypatch.setattr(WebhookTask, "webhook_task", sent.append)

    async def handler(request):
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(*scheduled)
    run = runs()[0]
    with DbSession.atomic() as db:
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    await scheduler._run_scheduler(*scheduled)
    assert len(sent) == 1 and "schedule_execution" not in sent[0].data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expired",
    [
        (BotScheduleRunningType.Infinite, False),
        (BotScheduleRunningType.Duration, False),
        (BotScheduleRunningType.Duration, True),
    ],
)
async def test_recurring_schedules_keep_existing_transport_and_stop_behavior(scheduled, monkeypatch, kind, expired):
    bot, schedule, association = scheduled
    with DbSession.atomic() as db:
        schedule = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule.id)).first()
        schedule.running_type = kind
        schedule.end_at = SafeDateTime.now() + timedelta(days=-1 if expired else 1)
        db.update(schedule)
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(bot, schedule, association)
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule.id)).first()
    assert saved.status == (BotScheduleStatus.Stopped if expired else BotScheduleStatus.Started)
    assert len(requests) == 1 and b"schedule_execution" not in requests[0].content and runs() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["project_deleted", "column_deleted"])
async def test_parent_revocation_between_attempts_prevents_retry(card_scheduled, monkeypatch, revocation):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    await scheduler._run_scheduler(*card_scheduled)
    run = runs()[0]
    with DbSession.atomic() as db:
        parent_class = Project if revocation == "project_deleted" else ProjectColumn
        parent_id = 3 if revocation == "project_deleted" else 4
        parent = db.exec(SqlBuilder.select.table(parent_class).where(parent_class.id == parent_id)).first()
        parent.deleted_at = SafeDateTime.now()
        db.update(parent)
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    await scheduler.recover_due_onetime_schedules()
    assert len(calls) == 1
    assert runs()[0].state == "pending" and runs()[0].attempt == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatching", [False, True])
async def test_expired_admission_reconciles_after_parent_revocation(card_scheduled, monkeypatch, dispatching):
    from langboard_shared.tasks.bots.utils.BotScheduleRunStore import claim_schedule_run, reserve_schedule_run

    now = SafeDateTime.now()
    run = reserve_schedule_run(card_scheduled[1], card_scheduled[2], now=now - timedelta(days=2))
    if dispatching:
        # A dispatch was in flight when authority disappeared. Never resend it.
        with DbSession.atomic() as db:
            run.expires_at = now + timedelta(days=1)
            db.update(run)
        assert claim_schedule_run(run.id, now=now - timedelta(minutes=10)) is not None
    with DbSession.atomic() as db:
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 3)).first()
        project.deleted_at = now
        db.update(project)
    dispatch = AsyncMock()
    monkeypatch.setattr(scheduler.BotTaskHelper, "run", dispatch)
    await scheduler.recover_due_onetime_schedules()
    saved = get_schedule_run(run.id)
    assert saved.state == ("uncertain" if dispatching else "failed")
    assert saved.error_code == ("dispatch_lease_expired" if dispatching else "retry_window_expired")
    assert saved.attempt == (1 if dispatching else 0)
    assert saved.lease_token is None and saved.lease_expires_at is None
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_resolves_current_occurrence_without_materializing_historical_runs(scheduled, monkeypatch):
    from langboard_shared.tasks.bots.utils.BotScheduleRunStore import reserve_schedule_run
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    now = SafeDateTime.now()
    current = reserve_schedule_run(scheduled[1], scheduled[2], now=now)
    # Earlier edits of the same schedule retain their accepted receipts.
    with DbSession.atomic() as db:
        for _ in range(128):
            db.insert(
                BotScheduleRun(
                    schedule_id=current.schedule_id,
                    scope_table=current.scope_table,
                    scope_id=current.scope_id,
                    occurrence_key=uuid4().hex,
                    state="accepted",
                    next_attempt_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
    original = scheduler.occurrence_key
    resolved = []

    def resolve(*args):
        resolved.append(args)
        return original(*args)

    monkeypatch.setattr(scheduler, "occurrence_key", resolve)
    dispatch = AsyncMock()
    monkeypatch.setattr(scheduler, "_run_scheduler", dispatch)
    loaded = []

    def record_load(session, instance):
        if isinstance(instance, BotScheduleRun):
            loaded.append(instance.id)

    event.listen(Session, "loaded_as_persistent", record_load)
    try:
        await scheduler.recover_due_onetime_schedules()
    finally:
        event.remove(Session, "loaded_as_persistent", record_load)
    dispatch.assert_awaited_once()
    assert len(resolved) == 1
    assert loaded == [current.id]
    assert len(runs()) == 129


@pytest.mark.asyncio
async def test_recovery_pages_current_associations_without_starving_later_admissions(scheduled, monkeypatch):
    from langboard_shared.tasks.bots.utils.BotScheduleRunStore import reserve_schedule_run

    associations = [scheduled[2]]
    with DbSession.atomic() as db:
        for _ in range(101):
            association = ProjectColumnBotSchedule(
                bot_schedule_id=scheduled[1].id,
                project_column_id=4,
            )
            db.insert(association)
            associations.append(association)
    # Most current schedules have never had an original cron admission.
    # They must neither create runs nor hide a due admission in a later page.
    expected = [associations[0], associations[99], associations[-1]]
    for association in expected:
        reserve_schedule_run(scheduled[1], association, now=SafeDateTime.now())
    dispatch = AsyncMock()
    monkeypatch.setattr(scheduler, "_run_scheduler", dispatch)
    await scheduler.recover_due_onetime_schedules()
    assert sorted(call.args[2].id for call in dispatch.await_args_list) == sorted(item.id for item in expected)
    assert len(runs()) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("schedule_id", 999), ("scope_id", 999), ("scope_table", "card_bot_schedule")])
async def test_recovery_rejects_current_key_with_mismatched_source_binding(scheduled, monkeypatch, field, value):
    from langboard_shared.tasks.bots.utils.BotScheduleRunStore import reserve_schedule_run

    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        setattr(run, field, value)
        db.update(run)
    dispatch = AsyncMock()
    monkeypatch.setattr(scheduler, "_run_scheduler", dispatch)
    await scheduler.recover_due_onetime_schedules()
    dispatch.assert_not_awaited()
