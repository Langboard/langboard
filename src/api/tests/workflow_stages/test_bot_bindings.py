"""Workflow bindings preserve native hooks, schedule eligibility and admission receipts."""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models
from langboard_shared.domain.models import (
    Bot,
    BotSchedule,
    Card,
    CardBotSchedule,
    CardBotScope,
    Project,
    ProjectColumn,
    User,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.bases import BotTriggerCondition
from langboard_shared.domain.models.BotSchedule import BotScheduleRunningType, BotScheduleStatus
from langboard_shared.domain.services.factory.WorkflowStageService import WorkflowStageService
from langboard_shared.tasks.bots.BotScheduleTask import _run_scheduler
from langboard_shared.tasks.bots.CardBotTask import _run_card_moved
from langboard_shared.tasks.bots.utils import BotTaskDataHelper
from langboard_shared.tasks.bots.utils.requests.BaseBotRequest import BotRequestOutcome
from langboard_shared.tasks.bots.WorkflowCompactionTask import (
    _claim_workflow_bot,
    _dispatch_workflow_bot,
    _utc,
    run_due_workflow_bots,
)
from sqlalchemy import create_engine


@pytest.fixture
def bots(monkeypatch):
    engine = create_engine("sqlite://")
    names = ["Bot", "BotSchedule", "CardBotSchedule", "CardBotScope", "ProjectBotScope", "ProjectColumnBotScope",
             "CardBotDefaultScope", "ProjectBotDefaultScope", "ProjectColumnBotDefaultScope", "User", "Project",
             "Card", "ProjectColumn", "WorkflowEffectRun", "WorkflowStageDefinition"]
    for name in names:
        getattr(models, name).__table__.create(engine)
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
        bot = Bot(name="Selected", bot_uname="selected", platform=BotPlatform.Default,
                  platform_running_type=BotPlatformRunningType.Default, app_api_token="test")
        db.insert(bot)
        hook = CardBotScope(bot_id=bot.id, card_id=card.id, conditions=[BotTriggerCondition.CardMoved])
        db.insert(hook)
        schedule = BotSchedule(bot_id=bot.id, interval_str="every 5 minutes", status=BotScheduleStatus.Started,
                               running_type=BotScheduleRunningType.Infinite)
        db.insert(schedule)
        association = CardBotSchedule(bot_schedule_id=schedule.id, card_id=card.id)
        db.insert(association)
        stage.entry_bots = [{"bot_uid": bot.get_uid(), "condition": "card_moved"}]
        stage.continuous_effects = {"bots": [{"bot_uid": bot.get_uid(), "condition": "bot_cron_scheduled",
                                            "schedule_uid": schedule.get_uid(), "interval_seconds": 120}]}
        db.update(stage)
    execute = AsyncMock(return_value=BotRequestOutcome.Accepted)
    helper = import_module("langboard_shared.tasks.bots.utils.BotTaskHelper")
    monkeypatch.setattr(helper, "create_request", lambda *args: SimpleNamespace(execute=execute))
    monkeypatch.setattr(helper.WebhookTask, "webhook_task", lambda *_: None)
    monkeypatch.setattr(BotTaskDataHelper, "create_card", lambda actor, project, card, *args: {
        "card_uid": card.get_uid(), "executor": {"type": "user"}, "project_uid": project.get_uid(),
    })
    yield SimpleNamespace(**locals())
    engine.dispose()


def history():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(WorkflowEffectRun).order_by(WorkflowEffectRun.id)).all()


@pytest.mark.asyncio
async def test_selected_entry_runs_actual_move_once_and_preserves_unselected_native_bot(bots):
    f = bots
    with DbSession.atomic() as db:
        other = Bot(name="Other", bot_uname="other", platform=BotPlatform.Default,
                    platform_running_type=BotPlatformRunningType.Default, app_api_token="test")
        db.insert(other)
        db.insert(CardBotScope(bot_id=other.id, card_id=f.card.id, conditions=[BotTriggerCondition.CardMoved]))
    await _run_card_moved(f.actor, f.project, f.card, f.old)
    assert f.execute.await_count == 2
    rows = history()
    assert len(rows) == 1 and rows[0].source == "bot_entry" and rows[0].status == "accepted"
    assert rows[0].result == {"accepted": 1} and rows[0].effects[-1] == "card_moved"
    # Re-delivery must not dispatch the selected workflow occurrence again.
    await _run_card_moved(f.actor, f.project, f.card, f.old)
    assert f.execute.await_count == 3 and len(history()) == 1


@pytest.mark.parametrize("case", ["frozen", "wrong_condition", "deleted_bot", "private", "inactive_actor"])
@pytest.mark.asyncio
async def test_entry_does_not_bypass_current_authority(bots, case):
    f = bots
    with DbSession.atomic() as db:
        if case == "frozen":
            f.hook.is_frozen = True
            db.update(f.hook)
        elif case == "wrong_condition":
            f.hook.conditions = [BotTriggerCondition.CardUpdated]
            db.update(f.hook)
        elif case == "deleted_bot":
            f.bot.deleted_at = SafeDateTime.now()
            db.update(f.bot)
        elif case == "private":
            f.card.visibility = "PRIVATE"
            f.card.owner_user_id = f.actor.id
            f.card.created_by_user_id = f.actor.id
            db.update(f.card)
        else:
            f.actor.activated_at = None
            db.update(f.actor)
    await _run_card_moved(f.actor, f.project, f.card, f.old)
    f.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_interval_and_native_cron_share_one_claim_then_interval_becomes_due(bots):
    f = bots
    assert await run_due_workflow_bots(now=f.now) == 1
    assert history()[0].status == "accepted" and history()[0].source == "bot_interval"
    await _run_scheduler(f.bot, f.schedule, f.association, f.card)
    assert await run_due_workflow_bots(now=f.now + timedelta(seconds=119)) == 0
    assert f.execute.await_count == 1
    assert await run_due_workflow_bots(now=f.now + timedelta(seconds=120)) == 1
    assert f.execute.await_count == 2 and len(history()) == 2


@pytest.mark.parametrize("case", ["stopped", "expired", "different_card", "private", "deleted_bot"])
@pytest.mark.asyncio
async def test_interval_rechecks_native_schedule_eligibility(bots, case):
    f = bots
    with DbSession.atomic() as db:
        if case == "stopped":
            f.schedule.status = BotScheduleStatus.Stopped
            db.update(f.schedule)
        elif case == "expired":
            f.schedule.running_type = BotScheduleRunningType.Duration
            f.schedule.start_at, f.schedule.end_at = f.now - timedelta(hours=2), f.now - timedelta(hours=1)
            db.update(f.schedule)
        elif case == "different_card":
            f.association.card_id = f.card.id + 1
            db.update(f.association)
        elif case == "private":
            f.card.visibility = "PRIVATE"
            f.card.owner_user_id = f.actor.id
            f.card.created_by_user_id = f.actor.id
            db.update(f.card)
        else:
            f.bot.deleted_at = f.now
            db.update(f.bot)
    assert await run_due_workflow_bots(now=f.now) == 0
    f.execute.assert_not_awaited()
    assert history() == []


def test_bot_binding_validation_is_additive_and_strict():
    assert "bots" not in WorkflowStageService.normalize_continuous_effects({})
    assert WorkflowStageService.normalize_bot_bindings([]) == []
    entry = {"bot_uid": "123456789ab", "condition": "card_moved"}
    assert WorkflowStageService.normalize_bot_bindings([entry]) == [entry]
    for invalid in [[entry, entry], [{**entry, "condition": "card_updated"}], [{**entry, "bot_uid": "bad"}]]:
        with pytest.raises(ValueError):
            WorkflowStageService.normalize_bot_bindings(invalid)
    for interval in [True, 1, 86401, 60.5]:
        with pytest.raises(ValueError):
            WorkflowStageService.normalize_bot_bindings([{"bot_uid": entry["bot_uid"], "condition": "bot_cron_scheduled",
                "schedule_uid": "123456789ac", "interval_seconds": interval}], continuous=True)


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
    assert options["bots"] == [{"uid": f.bot.get_uid(), "name": f.bot.name, "entry_available": True}]
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
async def test_claim_does_not_keep_authority_after_binding_revocation(bots):
    from langboard_shared.tasks.bots.WorkflowCompactionTask import _claim_workflow_bot, _dispatch_workflow_bot, _utc

    f = bots
    binding = f.stage.entry_bots[0]
    claim = _claim_workflow_bot(f.stage.id, f.card.id, binding, "bot_entry", f.now,
                               actor_id=f.actor.id, residency=_utc(f.card.workflow_stage_entered_at).isoformat())
    assert claim is not None
    with DbSession.atomic() as db:
        f.stage.entry_bots = []
        db.update(f.stage)
    await _dispatch_workflow_bot(claim, binding)
    f.execute.assert_not_awaited()
    assert history()[0].status == "skipped" and history()[0].error_code == "workflow_binding_changed"


@pytest.mark.asyncio
async def test_entry_hook_authorization_rereads_primary_without_replica_access(bots, monkeypatch):
    from langboard_shared.tasks.bots.WorkflowCompactionTask import _claim_workflow_bot, _dispatch_workflow_bot, _utc

    f = bots
    binding = f.stage.entry_bots[0]
    claim = _claim_workflow_bot(f.stage.id, f.card.id, binding, "bot_entry", f.now,
                               actor_id=f.actor.id, residency=_utc(f.card.workflow_stage_entered_at).isoformat())
    with DbSession.atomic() as db:
        f.hook.is_frozen = True
        db.update(f.hook)
    def unavailable_replica():
        raise AssertionError("Hook authorization must use the primary")
    monkeypatch.setattr(DbEngine, "get_readonly_engine", unavailable_replica)
    await _dispatch_workflow_bot(claim, binding)
    f.execute.assert_not_awaited()
    assert history()[0].status == "skipped"


@pytest.mark.asyncio
async def test_unsupported_one_time_schedule_keeps_native_behavior(bots, monkeypatch):
    from langboard_shared.tasks.bots import BotScheduleTask

    f = bots
    with DbSession.atomic() as db:
        f.schedule.running_type = BotScheduleRunningType.Onetime
        db.update(f.schedule)
    changes = []
    monkeypatch.setattr(BotScheduleTask.BotScheduleHelper, "change_status", lambda *args: changes.append(args))
    await _run_scheduler(f.bot, f.schedule, f.association, f.card)
    f.execute.assert_awaited_once()
    assert changes[0][-1] == BotScheduleStatus.Stopped
    assert history() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['new_residency','actor_inactive','card_archived','column_archived','column_deleted','card_private','bot_deleted','card_deleted'])
async def test_entry_claim_revocation(bots, case):
    f = bots
    binding = f.stage.entry_bots[0]
    claim = _claim_workflow_bot(f.stage.id, f.card.id, binding, 'bot_entry', f.now, actor_id=f.actor.id, residency=_utc(f.card.workflow_stage_entered_at).isoformat())
    assert claim is not None
    with DbSession.atomic() as db:
        if case == 'new_residency':
            f.card.workflow_stage_entered_at = f.now + timedelta(seconds=1)
            db.update(f.card)
        elif case == 'actor_inactive':
            f.actor.activated_at = None
            db.update(f.actor)
        elif case == 'card_archived':
            f.card.archived_at = f.now
            db.update(f.card)
        elif case == 'column_archived':
            f.column.is_archive = True
            db.update(f.column)
        elif case == 'column_deleted':
            f.column.deleted_at = f.now
            db.update(f.column)
        elif case == 'card_private':
            f.card.visibility = 'PRIVATE'
            f.card.owner_user_id = f.actor.id
            f.card.created_by_user_id = f.actor.id
            db.update(f.card)
        elif case == 'bot_deleted':
            f.bot.deleted_at = f.now
            db.update(f.bot)
        elif case == 'card_deleted':
            f.card.deleted_at = f.now
            db.update(f.card)
    await _dispatch_workflow_bot(claim, binding)
    rows = history()
    print({'case': case, 'transport_calls': f.execute.await_count, 'status': rows[0].status, 'error_code': rows[0].error_code})
    f.execute.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['actor_inactive','card_archived','column_archived','schedule_stopped','association_removed','bot_deleted'])
async def test_interval_claim_revocation(bots, case):
    f = bots
    binding = f.stage.continuous_effects['bots'][0]
    claim = _claim_workflow_bot(f.stage.id, f.card.id, binding, 'bot_interval', f.now)
    assert claim is not None
    with DbSession.atomic() as db:
        if case == 'actor_inactive':
            f.actor.activated_at = None
            db.update(f.actor)
        elif case == 'card_archived':
            f.card.archived_at = f.now
            db.update(f.card)
        elif case == 'column_archived':
            f.column.is_archive = True
            db.update(f.column)
        elif case == 'schedule_stopped':
            f.schedule.status = BotScheduleStatus.Stopped
            db.update(f.schedule)
        elif case == 'association_removed':
            f.association.card_id = f.card.id + 1
            db.update(f.association)
        elif case == 'bot_deleted':
            f.bot.deleted_at = f.now
            db.update(f.bot)
    await _dispatch_workflow_bot(claim, binding)
    rows = history()
    print({'case': case, 'transport_calls': f.execute.await_count, 'status': rows[0].status, 'error_code': rows[0].error_code})
    f.execute.assert_not_awaited()

@pytest.mark.asyncio
async def test_cancelled_entry_retains_uncertain_history_and_suppresses_replay(bots):
    import asyncio
    f = bots
    binding = f.stage.entry_bots[0]
    claim = _claim_workflow_bot(f.stage.id, f.card.id, binding, 'bot_entry', f.now, actor_id=f.actor.id, residency=_utc(f.card.workflow_stage_entered_at).isoformat())
    f.execute.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await _dispatch_workflow_bot(claim, binding)
    rows = history()
    assert rows[0].status == 'uncertain'
    assert rows[0].error_code == 'dispatch_cancelled'
    assert rows[0].lease_expires_at is None
    assert _claim_workflow_bot(f.stage.id, f.card.id, binding, 'bot_entry', f.now, actor_id=f.actor.id, residency=_utc(f.card.workflow_stage_entered_at).isoformat()) is None
    assert len(history()) == 1
