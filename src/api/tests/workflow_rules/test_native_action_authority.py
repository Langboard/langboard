"""Queued native effects recheck current action authority before changing work."""
import importlib.util
from datetime import timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain import models
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules, rows
from langboard_shared.infrastructure.repositories.factory.CheckitemTimerRecordRepository import (
    CheckitemTimerRecordRepository,
)
from langboard_shared.publishers import CheckitemPublisher
from langboard_shared.tasks.WorkflowRuleTask import run_rule


_spec = importlib.util.spec_from_file_location("native_action_bot_fixtures", Path(__file__).parents[1] / "workflow_stages/test_bot_bindings.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
bots = _fixtures.bots


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["checkitems.complete", "timers.stop", "author.notify", "bot.invoke"])
@pytest.mark.parametrize("revoke_update", [False, True])
async def test_queued_effect_rechecks_action_specific_authority(bots, monkeypatch, kind, revoke_update):
    f = bots
    tables = [model.__table__ for model in (models.Checklist, models.Checkitem, models.CheckitemTimerRecord)]
    for table in tables:
        for foreign_key in table.foreign_keys:
            if foreign_key.column.table not in tables:
                tables.append(foreign_key.column.table)
    tables[0].metadata.create_all(f.engine, tables=tables)
    with DbSession.atomic() as db:
        owner = models.User(email="owner@example.test", firstname="Owner", lastname="", username="owner",
                            password="unused", activated_at=f.now)
        db.insert(owner)
        f.project.owner_id = owner.id
        db.update(f.project)
        db.insert(models.ProjectAssignedUser(project_id=f.project.id, user_id=f.actor.id))
        role = models.ProjectRole(project_id=f.project.id, user_id=f.actor.id,
                                  actions=[ProjectRoleAction.Read.value, ProjectRoleAction.CardUpdate.value])
        db.insert(role)
        checklist = models.Checklist(card_id=f.card.id, title="Acceptance")
        db.insert(checklist)
        item = models.Checkitem(checklist_id=checklist.id, title="Running work", user_id=f.actor.id,
                               status=CheckitemStatus.Started)
        db.insert(item)
        db.insert(models.CheckitemTimerRecord(checkitem_id=item.id, status=CheckitemStatus.Started,
                                             created_at=f.now - timedelta(seconds=60)))
        if kind != "bot.invoke":
            replace_rules(db, f.stage, [{"name": "Native effect", "is_enabled": True,
                "trigger": {"kind": "stage_entered"}, "condition_match": "all", "conditions": [],
                "actions": [{"kind": kind, "executor": {"kind": "event_actor"}, "config": {},
                             "requires_previous_success": False}]}])
    repo = SimpleNamespace(checkitem_timer_record=CheckitemTimerRecordRepository(None, None))
    checkitem = CheckitemService(None, None, repo)
    notify = Mock(return_value=True)
    worker = import_module("langboard_shared.tasks.WorkflowRuleTask")
    monkeypatch.setattr(worker, "DomainService", lambda: SimpleNamespace(
        checkitem=checkitem, notification=SimpleNamespace(notify_workflow_stage_entered=notify)))
    monkeypatch.setattr(CheckitemPublisher, "workflow_effects_applied", Mock())
    run_id = _fixtures.reserve(f)
    assert _fixtures.reserve(f) == run_id
    if revoke_update:
        with DbSession.atomic() as db:
            role.actions = [ProjectRoleAction.Read.value]
            db.update(role)
    await run_rule(run_id)
    await run_rule(run_id)
    denied = revoke_update and kind in {"checkitems.complete", "timers.stop"}
    with DbSession.use(readonly=False) as db:
        claim = rows(db, models.WorkflowActionRun)[0]
        current = db.exec(SqlBuilder.select.table(models.Checkitem).where(models.Checkitem.id == item.id)).first()
        assert rows(db, models.ProjectAssignedUser, project_id=f.project.id, user_id=f.actor.id)
        assert rows(db, models.ProjectRole, project_id=f.project.id, user_id=f.actor.id)[0].is_granted(ProjectRoleAction.Read)
        assert claim.status == ("skipped" if denied else "accepted" if kind == "bot.invoke" else "succeeded")
        assert claim.error_code == ("workflow_actor_revoked" if denied else None)
        mutates = kind in {"checkitems.complete", "timers.stop"} and not denied
        assert current.status == (CheckitemStatus.Stopped if mutates else CheckitemStatus.Started)
        assert current.is_checked == (kind == "checkitems.complete" and not denied)
        assert len(rows(db, models.CheckitemTimerRecord)) == (2 if mutates else 1)
    assert notify.call_count == int(kind == "author.notify")
    assert f.execute.await_count == int(kind == "bot.invoke")
