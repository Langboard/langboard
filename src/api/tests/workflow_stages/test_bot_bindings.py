"""Workflow bindings preserve native hooks, schedule eligibility and admission receipts."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models
from langboard_shared.domain.constants.DomainEventType import DomainEventType
from langboard_shared.domain.models import (
    Bot,
    BotSchedule,
    Card,
    CardBotSchedule,
    CardBotScope,
    Project,
    ProjectColumn,
    User,
    WorkflowActionRun,
    WorkflowRuleRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.BotSchedule import BotScheduleRunningType, BotScheduleStatus
from langboard_shared.domain.models.BotScheduleRun import BotScheduleRun
from langboard_shared.domain.services.factory.WorkflowRuleService import admit, replace_rules, rows, utc
from langboard_shared.domain.services.factory.WorkflowStageService import WorkflowStageService
from langboard_shared.tasks.bots.BotScheduleTask import _run_scheduler
from langboard_shared.tasks.bots.utils import BotTaskDataHelper
from langboard_shared.tasks.bots.utils.requests.BaseBotRequest import BotRequestOutcome
from langboard_shared.tasks.WorkflowRuleTask import run_due_rules, run_rule
from sqlalchemy import create_engine, text


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def bots(monkeypatch, request):
    url = request.param
    if url == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = "workflow_bot_test_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={
            "options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=5000",
        })

    def cleanup():
        try:
            if schema:
                with engine.begin() as db:
                    db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            engine.dispose()

    request.addfinalizer(cleanup)
    names = ["Bot", "BotSchedule", "CardBotSchedule", "CardBotScope", "ProjectBotScope", "ProjectColumnBotScope",
             "CardBotDefaultScope", "ProjectBotDefaultScope", "ProjectColumnBotDefaultScope", "User", "Project",
             "Card", "ProjectColumn", "WorkflowEffectRun", "WorkflowStageDefinition", "BotScheduleRun",
             "ProjectAssignedUser", "ProjectRole", "WorkflowRule", "WorkflowRuleAction", "WorkflowRuleCondition", "WorkflowRuleRun", "WorkflowActionRun"]
    tables = [getattr(models, name).__table__ for name in names]
    # SQLite permits absent FK targets; PostgreSQL requires the actual dependency tables.
    for table in tables:
        for foreign_key in table.foreign_keys:
            dependency = foreign_key.column.table
            if dependency not in tables:
                tables.append(dependency)
    tables[0].metadata.create_all(engine, tables=tables)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        actor = User(email="test@example.test", firstname="Test", lastname="", username="test", password="unused", activated_at=now)
        db.insert(actor)
        project = Project(owner_id=actor.id, title="Project")
        db.insert(project)
        stage = WorkflowStageDefinition(key="active", name="Active")
        db.insert(stage)
        old = ProjectColumn(project_id=project.id, name="Old", order=0)
        column = ProjectColumn(project_id=project.id, name="Active", order=1, workflow_stage=stage.key)
        db.insert(old)
        db.insert(column)
        card = Card(project_id=project.id, project_column_id=column.id, title="Card", order=0,
                    workflow_stage_entered_key=stage.key, workflow_stage_entered_at=now)
        db.insert(card)
        # A different valid card preserves association-mismatch semantics with PostgreSQL foreign keys.
        other_card = Card(project_id=project.id, project_column_id=old.id, title="Other card", order=1)
        db.insert(other_card)
        bot = Bot(name="Selected", bot_uname="selected", platform=BotPlatform.Default,
                  platform_running_type=BotPlatformRunningType.Default, app_api_token="test")
        db.insert(bot)
        hook = CardBotScope(bot_id=bot.id, card_id=card.id, conditions=[DomainEventType.CardMoved])
        db.insert(hook)
        schedule = BotSchedule(bot_id=bot.id, interval_str="every 5 minutes", status=BotScheduleStatus.Started,
                               running_type=BotScheduleRunningType.Infinite)
        db.insert(schedule)
        association = CardBotSchedule(bot_schedule_id=schedule.id, card_id=card.id)
        db.insert(association)
        replace_rules(db, stage, [definition(bot.get_uid())])
    execute = AsyncMock(return_value=BotRequestOutcome.Accepted)
    helper = import_module("langboard_shared.tasks.bots.utils.BotTaskHelper")
    monkeypatch.setattr(helper, "create_request", lambda *args: SimpleNamespace(execute=execute))
    monkeypatch.setattr(helper.WebhookTask, "webhook_task", lambda *_: None)
    monkeypatch.setattr(BotTaskDataHelper, "create_card", lambda actor, project, card, *args: {
        "card_uid": card.get_uid(), "executor": {"type": "user"}, "project_uid": project.get_uid(),
    })
    yield SimpleNamespace(**locals())


def definition(bot_uid, trigger=None, schedule_uid=None):
    config = {"bot_uid": bot_uid}
    if schedule_uid:
        config["schedule_uid"] = schedule_uid
    return {"name": "Selected bot", "is_enabled": True, "trigger": trigger or {"kind": "stage_entered"},
            "condition_match": "all", "conditions": [], "actions": [{"kind": "bot.invoke", "executor": {"kind": "bot", "uid": bot_uid},
            "config": config, "requires_previous_success": False}]}


def history():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(WorkflowActionRun).order_by(WorkflowActionRun.id)).all()


def reserve(f, *, event="stage_entered", identity="one-event", system=False):
    with DbSession.atomic() as db:
        return admit(db, None if system else f.actor, f.card, event, event_id=identity, occurred_at=f.now)[0]


def configure(f, *, trigger=None, schedule=False):
    with DbSession.atomic() as db:
        replace_rules(db, f.stage, [definition(f.bot.get_uid(), trigger, f.schedule.get_uid() if schedule else None)])

def test_migration_preserves_old_policy_and_refuses_binding_loss():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022050000-a1b2c3d4e5f6.py"
    spec = importlib.util.spec_from_file_location("workflow_bot_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE workflow_stage_definition(id BIGINT PRIMARY KEY, entry_effects JSON, continuous_effects JSON)"))
        connection.execute(text("INSERT INTO workflow_stage_definition VALUES (1, '[\"stop_running_timers\"]', '{}')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(text("SELECT entry_bots, entry_effects FROM workflow_stage_definition")).one() == ("[]", '["stop_running_timers"]')
        connection.execute(text("UPDATE workflow_stage_definition SET entry_bots='[{\"bot_uid\":\"123456789ab\"}]'"))
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        connection.execute(text("UPDATE workflow_stage_definition SET entry_bots='[]'"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
    engine.dispose()

def test_options_include_only_visible_card_schedules_and_minimal_bot_identity(bots):
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext

    f = bots
    contexts = {f.project.id: CardVisibilityContext(CollaborationChannel.Api, True, True, True, actor_user_id=f.actor.id)}
    service = WorkflowStageService(lambda _: SimpleNamespace(resolve_work_visibility_contexts=lambda *_: contexts), None, None)
    options = service.get_bot_options(f.actor)
    assert options["bots"] == [{"uid": f.bot.get_uid(), "name": f.bot.name, "entry_available": True, "policy_available": True}]
    assert options["schedules"] == [{"uid": f.schedule.get_uid(), "bot_uid": f.bot.get_uid(), "card_uid": f.card.get_uid(),
                                     "name": f.card.title, "is_available": True}]
    contexts.clear()
    assert service.get_bot_options(f.actor)["schedules"] == []

def test_bot_options_route_requires_both_settings_permissions():
    from langboard.routes.settings.WorkflowStageSettingsApi import get_workflow_bot_options
    from langboard_shared.core.filter import AuthFilter
    from langboard_shared.filter import RoleFilter

    _, actions, _, bypass = RoleFilter.get_filtered(get_workflow_bot_options)
    assert actions == ["workflow_stage_read", "bot_read"] and bypass is False
    assert AuthFilter.get_filtered(get_workflow_bot_options) == "admin"

@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [BotRequestOutcome.Accepted, BotRequestOutcome.RetryableFailure])
async def test_unsupported_one_time_schedule_keeps_native_behavior(bots, monkeypatch, outcome):
    from langboard_shared.tasks.bots import BotScheduleTask

    f = bots
    with DbSession.atomic() as db:
        f.schedule.running_type = BotScheduleRunningType.Onetime
        f.schedule.start_at = f.now - timedelta(minutes=1)
        db.update(f.schedule)
    changes = []
    monkeypatch.setattr(BotScheduleTask.BotScheduleHelper, "change_status", lambda *args: changes.append(args))

    async def execute():
        with DbSession.atomic() as db:
            runs = db.exec(SqlBuilder.select.table(BotScheduleRun)).all()
            assert len(runs) == 1
            run = runs[0]
            assert run.state == "dispatching" and run.attempt == 1
        assert changes == []  # Native schedules stop only after durable acceptance.
        return outcome

    f.execute.side_effect = execute
    await _run_scheduler(f.bot, f.schedule, f.association, f.card)
    f.execute.assert_awaited_once()
    with DbSession.atomic() as db:
        runs = db.exec(SqlBuilder.select.table(BotScheduleRun)).all()
        assert len(runs) == 1
        run = runs[0]
        assert run.schedule_id == f.schedule.id and run.scope_id == f.association.id
        assert run.scope_table == f.association.__tablename__ and run.attempt == 1
        assert run.lease_token is None and run.lease_expires_at is None
        if outcome == BotRequestOutcome.Accepted:
            assert run.state == "accepted" and run.error_code is None
            assert len(changes) == 1 and changes[0][-1] == BotScheduleStatus.Stopped
        else:
            assert run.state == "pending" and run.error_code == "retryable_failure"
            assert changes == []
    assert history() == []

def test_policy_options_support_comment_only_native_hook(bots):
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext

    f = bots
    with DbSession.atomic() as db:
        f.hook.conditions = [DomainEventType.CardCommentAdded]
        db.update(f.hook)
    contexts = {f.project.id: CardVisibilityContext(CollaborationChannel.Api, True, True, True, actor_user_id=f.actor.id)}
    service = WorkflowStageService(lambda _: SimpleNamespace(resolve_work_visibility_contexts=lambda *_: contexts), None, None)
    option = service.get_bot_options(f.actor)["bots"][0]
    assert option["policy_available"] is True and option["entry_available"] is False


@pytest.mark.asyncio
async def test_common_entry_duplicate_identity_keeps_one_accepted_claim(bots):
    f = bots
    run = reserve(f)
    assert reserve(f) == run
    await run_rule(run)
    await run_rule(run)
    f.execute.assert_awaited_once()
    assert len(history()) == 1 and history()[0].status == "accepted"
    assert history()[0].attempt == 1 and history()[0].result == {"accepted": 1}


@pytest.mark.parametrize("case", ["frozen", "condition", "binding", "stage", "new_residency", "actor_inactive", "card_archived", "column_archived", "column_deleted", "card_private", "bot_deleted", "card_deleted", "project_deleted"])
@pytest.mark.parametrize("event", ["stage_entered", "card_comment_added"])
@pytest.mark.asyncio
async def test_common_claim_rechecks_all_native_authority(bots, case, event):
    f = bots
    if event != "stage_entered":
        configure(f, trigger={"kind": "event", "event_type": event})
        with DbSession.atomic() as db:
            f.hook.conditions = [DomainEventType(event)]
            db.update(f.hook)
    run = reserve(f, event=event)
    with DbSession.atomic() as db:
        if case == "frozen":
            f.hook.is_frozen = True
            db.update(f.hook)
        elif case == "condition":
            f.hook.conditions = [DomainEventType.CardUpdated]
            db.update(f.hook)
        elif case == "binding":
            replace_rules(db, f.stage, [])
        elif case == "stage":
            f.column.workflow_stage = None
            db.update(f.column)
        elif case == "new_residency":
            f.card.workflow_stage_entered_at = f.now + timedelta(seconds=1)
            db.update(f.card)
        elif case == "actor_inactive":
            f.actor.activated_at = None
            db.update(f.actor)
        elif case == "card_archived":
            f.card.archived_at = f.now
            db.update(f.card)
        elif case == "column_archived":
            f.column.is_archive = True
            db.update(f.column)
        elif case == "column_deleted":
            f.column.deleted_at = f.now
            db.update(f.column)
        elif case == "card_private":
            f.card.visibility, f.card.owner_user_id, f.card.created_by_user_id = "PRIVATE", f.actor.id, f.actor.id
            db.update(f.card)
        elif case == "bot_deleted":
            f.bot.deleted_at = f.now
            db.update(f.bot)
        elif case == "project_deleted":
            f.project.deleted_at = f.now
            db.update(f.project)
        else:
            f.card.deleted_at = f.now
            db.update(f.card)
    await run_rule(run)
    f.execute.assert_not_awaited()
    assert history()[0].status == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["stopped", "expired", "different_card", "private", "deleted", "column_archived"])
async def test_interval_rechecks_native_schedule_eligibility(bots, case):
    f = bots
    configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    run = reserve(f, event="interval", system=True)
    with DbSession.atomic() as db:
        if case == "stopped":
            f.schedule.status = BotScheduleStatus.Stopped
            db.update(f.schedule)
        elif case == "expired":
            f.schedule.running_type = BotScheduleRunningType.Duration
            f.schedule.start_at, f.schedule.end_at = f.now - timedelta(hours=2), f.now - timedelta(hours=1)
            db.update(f.schedule)
        elif case == "different_card":
            f.association.card_id = f.other_card.id
            db.update(f.association)
        elif case == "private":
            f.card.visibility, f.card.owner_user_id, f.card.created_by_user_id = "PRIVATE", f.actor.id, f.actor.id
            db.update(f.card)
        elif case == "column_archived":
            f.column.is_archive = True
            db.update(f.column)
        else:
            f.bot.deleted_at = f.now
            db.update(f.bot)
    await run_rule(run)
    f.execute.assert_not_awaited()
    assert history()[0].status == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [BotRequestOutcome.Accepted, BotRequestOutcome.Uncertain])
async def test_interval_native_cron_and_rule_cron_share_stable_due_identity(bots, outcome):
    f = bots
    f.execute.return_value = outcome
    configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    await _run_scheduler(f.bot, f.schedule, f.association, f.card)
    f.execute.assert_not_awaited()
    assert await run_due_rules(now=f.now + timedelta(seconds=120)) == 1
    assert await run_due_rules(now=f.now + timedelta(seconds=120)) == 0
    assert await run_due_rules(now=f.now + timedelta(seconds=239)) == 0
    # Accepted is a dispatch receipt; native recurring schedules allow the next slot.
    # An ambiguous transport result remains suppressed to avoid blind resubmission.
    expected_count = 2 if outcome == BotRequestOutcome.Accepted else 1
    assert await run_due_rules(now=f.now + timedelta(seconds=240)) == expected_count - 1
    assert f.execute.await_count == expected_count
    assert len({claim.idempotency_key for claim in history()}) == expected_count
    assert {claim.status for claim in history()} == {outcome.value}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [BotRequestOutcome.Accepted, BotRequestOutcome.Uncertain])
async def test_transport_outcomes_are_truthful_and_not_automatically_replayed(bots, outcome):
    f = bots
    f.execute.return_value = outcome
    run = reserve(f)
    await run_rule(run)
    await run_rule(run)
    expected = "failed" if outcome == BotRequestOutcome.RetryableFailure else outcome.value
    assert history()[0].status == expected
    f.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_transport_keeps_uncertain_claim_without_replay(bots):
    import asyncio
    f = bots
    f.execute.side_effect = asyncio.CancelledError
    run = reserve(f)
    with pytest.raises(asyncio.CancelledError):
        await run_rule(run)
    assert history()[0].status == "uncertain" and history()[0].error_code == "dispatch_cancelled"
    await run_rule(run)
    f.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_primary_hook_reread_does_not_use_replica(bots, monkeypatch):
    f = bots
    run = reserve(f)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: (_ for _ in ()).throw(AssertionError("replica authorization")))
    await run_rule(run)
    f.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_membership_revocation_denies_queued_common_claim(bots):
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
    f = bots
    with DbSession.atomic() as db:
        owner = User(email="owner@example.test", firstname="Owner", lastname="", username="owner", password="unused", activated_at=f.now)
        db.insert(owner)
        f.project.owner_id = owner.id
        db.update(f.project)
        member = models.ProjectAssignedUser(project_id=f.project.id, user_id=f.actor.id)
        db.insert(member)
        db.insert(models.ProjectRole(project_id=f.project.id, user_id=f.actor.id, actions=[ProjectRoleAction.Read.value]))
    run = reserve(f)
    with DbSession.atomic() as db:
        db.delete(member)
    await run_rule(run)
    assert history()[0].error_code == "workflow_actor_revoked"
    f.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_native_scopes_do_not_duplicate_selected_dispatch(bots):
    f = bots
    with DbSession.atomic() as db:
        db.insert(models.ProjectBotScope(bot_id=f.bot.id, project_id=f.project.id, conditions=[DomainEventType.CardMoved]))
    await run_rule(reserve(f))
    f.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_definite_rejection_retries_same_action_identity_with_bounded_backoff(bots):
    f = bots
    f.execute.return_value = BotRequestOutcome.RetryableFailure
    run_id = reserve(f)
    initial_key = history()[0].idempotency_key
    for attempt in range(1, 4):
        await run_rule(run_id)
        claim = history()[0]
        assert claim.attempt == attempt and claim.idempotency_key == initial_key
        assert len(history()) == 1 and len(claim.attempts) == attempt
        if attempt < 3:
            assert claim.status == "queued"
            with DbSession.atomic() as db:
                run = rows(db, WorkflowRuleRun)[0]
                assert utc(run.next_attempt_at) > utc(SafeDateTime.now())
                run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
                db.update(run)
        else:
            assert claim.status == "failed" and claim.error_code == "retry_exhausted"
    await run_rule(run_id)
    assert f.execute.await_count == 3


@pytest.mark.asyncio
async def test_simultaneous_same_event_claims_admit_one_dispatch(bots):
    f = bots
    if f.engine.dialect.name != "postgresql":
        pytest.skip("Concurrent row-lock admission requires PostgreSQL")
    start = Barrier(2)

    def claim():
        start.wait(timeout=5)
        return reserve(f)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim(), range(2)))
    assert claims[0] == claims[1]
    assert len(history()) == 1
    await run_rule(claims[0])
    await run_rule(claims[1])
    f.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_interval_slots_cannot_reset_exhausted_retry_budget(bots):
    f = bots
    configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    f.execute.return_value = BotRequestOutcome.RetryableFailure
    assert await run_due_rules(now=f.now + timedelta(seconds=120)) == 1
    for _ in range(2):
        with DbSession.atomic() as db:
            pending = rows(db, WorkflowRuleRun)[0]
            pending.next_attempt_at = f.now
            db.update(pending)
        await run_rule(pending.id, now=f.now + timedelta(seconds=120))
    assert history()[0].error_code == "retry_exhausted"
    assert await run_due_rules(now=f.now + timedelta(seconds=240)) == 0
    assert f.execute.await_count == 3 and len(history()) == 1
