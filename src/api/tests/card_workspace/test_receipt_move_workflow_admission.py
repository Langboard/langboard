"""Receipt moves reserve common workflow work before best-effort publication."""

import os
from datetime import UTC, datetime
from functools import wraps
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard.card_workspace.application import execution_receipts as receipts
from langboard_shared.core.broadcast import DispatcherQueue
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models
from langboard_shared.domain.models.bases import BaseGraphApprovalRequestModel
from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules, rows
from langboard_shared.Env import Env
from langboard_shared.helpers import ModelHelper
from langboard_shared.tasks.activities import CardActivityTask
from langboard_shared.tasks.bots import CardBotTask
from langboard_shared.tasks.WorkflowRuleTask import run_due_rules
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@pytest.fixture
def receipt_move(monkeypatch):
    url = os.getenv("LANGBOARD_OUTBOX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set LANGBOARD_OUTBOX_TEST_DATABASE_URL to disposable PostgreSQL")
    schema = "receipt_move_workflow_" + uuid4().hex
    admin = create_engine(url)
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=3000"}
    )
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        tables = set()

        def include(table):
            if table in tables:
                return
            tables.add(table)
            for foreign_key in table.foreign_keys:
                include(foreign_key.column.table)

        for name in (
            "Card", "CardAssignedProjectLabel", "CardAssignedUser", "CardRelationship", "CardVerificationRecord",
            "Checklist", "Checkitem", "CheckitemTimerRecord", "ProjectExecutionBinding", "ProjectRole",
            "WebhookSetting", "WorkflowStageDefinition", "WorkflowRule", "WorkflowRuleCondition",
            "WorkflowRuleAction", "WorkflowRuleRun", "WorkflowActionRun",
        ):
            include(getattr(models, name).__table__)
        for model in ModelHelper.get_models_by_base_class(BaseGraphApprovalRequestModel):
            include(model.__table__)
        models.Card.metadata.create_all(engine, tables=list(tables))
        migration = import_module("langboard.migrations.versions.20260924220000-7ad15b1d0b70")
        with engine.begin() as db:
            for column in ("column_semantic_ids", "prerequisite_relationship_type_id", "webhook_id"):
                db.execute(text(f'ALTER TABLE project_execution_binding DROP COLUMN "{column}"'))
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
            migration.upgrade()
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
        with DbSession.atomic() as db:
            actor = models.User(
                email="receipt@example.invalid", firstname="Receipt", lastname="Owner", password="unused",
                activated_at=SafeDateTime.now(),
            )
            db.insert(actor)
            project = models.Project(owner_id=actor.id, title="Receipt movement")
            db.insert(project)
            stage = models.WorkflowStageDefinition(key="active", name="Active")
            db.insert(stage)
            source = models.ProjectColumn(project_id=project.id, name="Execution", workflow_stage=stage.key)
            target = models.ProjectColumn(project_id=project.id, name="Review receipt", workflow_stage=stage.key)
            db.insert(source)
            db.insert(target)
            card = models.Card(
                project_id=project.id, project_column_id=source.id, title="Submitted work", visibility="SHARED",
                description=EditorContentModel(content="User-authored description"), last_change_seq=5,
                workflow_stage_entered_key=stage.key, workflow_stage_entered_at=SafeDateTime.now(),
            )
            db.insert(card)
            checklist = models.Checklist(card_id=card.id, title="Acceptance")
            db.insert(checklist)
            item = models.Checkitem(checklist_id=checklist.id, title="Native workflow action", is_checked=False)
            db.insert(item)
            db.insert(models.ProjectExecutionBinding(
                project_id=project.id, is_enabled=True,
                column_semantic_ids={str(source.id): "active", str(target.id): "review"},
            ))
            db.exec(text("INSERT INTO card_execution_generation VALUES (:card_id, 5)"), params={"card_id": card.id})
            replace_rules(db, stage, [{
                "name": "Complete checklist on move", "trigger": {"kind": "event", "event_type": "card_moved"},
                "condition_match": "all", "conditions": [], "actions": [{
                    "kind": "checkitems.complete", "executor": {"kind": "event_actor"},
                    "config": {}, "requires_previous_success": False,
                }],
            }], configured_by_id=actor.id)

        # Keep native admission; replace only unrelated broker transports.
        native_move = CardBotTask.card_moved

        @wraps(native_move)
        def admit_without_broker(*args, **kwargs):
            return native_move._workflow_prepare(*args, **kwargs)

        monkeypatch.setattr(CardBotTask, "card_moved", admit_without_broker)
        monkeypatch.setattr(CardBotTask, "enqueue_card_moved_webhook", lambda *_: None)
        monkeypatch.setattr(CardActivityTask, "card_moved", lambda *_: None)
        packets = []

        def transport(model):
            assert not DbSession.has_active_transaction()
            packets.append(model.model_dump()["data"]["data"])

        monkeypatch.setattr(DispatcherQueue, "put", transport)
        form = receipts.PutExecutionReceiptForm(
            status="review_ready", summary="Pull request submitted", occurred_at=datetime(2026, 10, 27, tzinfo=UTC),
            review={"changed": "Pull request submitted", "verified": "Database regression",
                    "remaining": "Human acceptance", "evidence_refs": ["https://example.invalid/pull/1"]},
        )
        yield SimpleNamespace(**locals())
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def store(f, form=None):
    return receipts.store_execution_receipt(
        f.project.get_uid(), f.card.get_uid(), 5, form or f.form,
        f"langboard:{f.project.get_uid()}:{f.card.get_uid()}:5:receipt", f.actor,
    )


def committed(f):
    with f.engine.connect() as db:
        card = db.execute(select(
            models.Card.project_column_id, models.Card.last_change_seq, models.Card.workflow_stage_entered_key,
            models.Card.workflow_stage_entered_at, models.Card.description,
        ).where(models.Card.id == f.card.id)).one()
        count = db.execute(text("SELECT count(*) FROM execution_receipt")).scalar_one()
    with Session(f.engine) as db:
        runs = db.scalars(select(models.WorkflowRuleRun)).all()
        actions = db.scalars(select(models.WorkflowActionRun)).all()
    return card, count, runs, actions


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["order", "work_state", None])
async def test_receipt_move_reserves_before_transport_and_native_drain(receipt_move, monkeypatch, caplog, failure):
    f = receipt_move
    attempted, reservations_at_transport = [], []

    def transport(model):
        assert not DbSession.has_active_transaction()
        packet = model.model_dump()["data"]["data"]
        attempted.append(packet)
        card, count, runs, actions = committed(f)
        assert card[0] == f.target.id and count == 1
        reservations_at_transport.append((len(runs), len(actions)))
        if failure == "order" and "to_column_uid" in packet or failure == "work_state" and "work_state" in packet:
            raise RuntimeError(f"{failure} transport unavailable")

    monkeypatch.setattr(DispatcherQueue, "put", transport)
    result = store(f)
    card, count, runs, actions = committed(f)
    assert result["created"] is True and result["moved_to_review"] is True
    assert card[:3] == (f.target.id, 5, "active") and count == 1
    assert card[3] == f.card.workflow_stage_entered_at
    assert card[4].content == "User-authored description"
    assert any("to_column_uid" in packet for packet in attempted)
    if failure == "work_state":
        assert any("work_state" in packet for packet in attempted)
    if failure:
        assert f"{failure} transport unavailable" in caplog.text
    assert len(runs) == len(actions) == 1, "Committed receipt move lost durable card_moved admission"
    assert set(reservations_at_transport) == {(1, 1)}, "Reservation must precede every after-commit transport"
    run, action = runs[0], actions[0]
    assert run.event_type == "card_moved" and run.status == action.status == "queued"
    assert run.event_context["metadata"]["old_project_column_uid"] == f.source.get_uid()
    assert action.kind == "checkitems.complete" and action.attempt == 0

    replay = store(f, f.form.model_copy(update={"occurred_at": SafeDateTime.now()}))
    assert replay["created"] is False and replay["moved_to_review"] is False
    assert replay["receipt"]["occurred_at"] == result["receipt"]["occurred_at"]
    with pytest.raises(receipts.ApiException.Conflict_409):
        store(f, f.form.model_copy(update={"summary": "Changed semantic content"}))
    assert [row.id for row in committed(f)[2]] == [run.id]
    assert [row.idempotency_key for row in committed(f)[3]] == [action.idempotency_key]

    assert await run_due_rules(limit=1) == 1
    assert await run_due_rules(limit=1) == 0
    _, _, runs, actions = committed(f)
    assert runs[0].status == actions[0].status == "succeeded"
    assert actions[0].result == {"completed": 1, "stopped": 0} and actions[0].attempt == 1
    with DbSession.use(readonly=False) as db:
        item = db.exec(SqlBuilder.select.table(models.Checkitem).where(models.Checkitem.id == f.item.id)).first()
        assert item.is_checked is True
    assert store(f)["created"] is False
    assert len(committed(f)[2]) == len(committed(f)[3]) == 1


def test_receipt_move_rollback_discards_move_receipt_and_reservation(receipt_move):
    f = receipt_move
    with pytest.raises(RuntimeError, match="rollback original receipt"):
        with DbSession.atomic() as db:
            assert store(f)["moved_to_review"] is True
            assert len(rows(db, models.WorkflowRuleRun)) == len(rows(db, models.WorkflowActionRun)) == 1
            card, count, runs, actions = committed(f)
            assert card[0] == f.source.id and count == 0 and runs == actions == []
            assert f.packets == []
            raise RuntimeError("rollback original receipt")
    card, count, runs, actions = committed(f)
    assert card[0] == f.source.id and count == 0 and runs == actions == []
    assert f.packets == []


def test_failed_receipt_does_not_move_or_reserve_workflow(receipt_move):
    f = receipt_move
    result = store(f, f.form.model_copy(update={"status": "failed"}))
    card, count, runs, actions = committed(f)
    assert result["created"] is True and result["moved_to_review"] is False
    assert card[0] == f.source.id and count == 1 and runs == actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [False, True])
async def test_cross_stage_receipt_uses_native_residency_and_atomic_entry_effects(receipt_move, monkeypatch, rollback):
    f = receipt_move
    with DbSession.atomic() as db:
        review = models.WorkflowStageDefinition(key="review", name="Review")
        db.insert(review)
        f.target.workflow_stage = review.key
        db.update(f.target)
        replace_rules(db, review, [{
            "name": name, "trigger": trigger, "condition_match": "all", "conditions": [], "actions": [{
                "kind": kind, "executor": {"kind": "event_actor"}, "config": {}, "requires_previous_success": False,
            }],
        } for name, trigger, kind in (
            ("Complete on entry", {"kind": "stage_entered"}, "checkitems.complete"),
            ("Stop on move", {"kind": "event", "event_type": "card_moved"}, "timers.stop"),
        )], configured_by_id=f.actor.id)

    def transport(model):
        assert not DbSession.has_active_transaction()
        packet = model.model_dump()["data"]["data"]
        f.packets.append(packet)
        if "to_column_uid" in packet:
            raise RuntimeError("cross-stage order transport unavailable")

    monkeypatch.setattr(DispatcherQueue, "put", transport)

    def move():
        with DbSession.atomic() as db:
            result = store(f)
            current = db.exec(SqlBuilder.select.table(models.Card).where(models.Card.id == f.card.id)
                              .execution_options(populate_existing=True)).first()
            assert current.project_column_id == f.target.id
            assert current.workflow_stage_entered_key == "review", "Receipt move left stale stage residency"
            assert current.workflow_stage_entered_at > f.card.workflow_stage_entered_at
            runs = {run.event_type: run for run in rows(db, models.WorkflowRuleRun)}
            assert set(runs) == {"stage_entered", "card_moved"}
            assert runs["stage_entered"].status == "succeeded" and runs["card_moved"].status == "queued"
            claims = {claim.rule_run_id: claim for claim in rows(db, models.WorkflowActionRun)}
            assert claims[runs["stage_entered"].id].result == {"completed": 1, "stopped": 0}
            assert claims[runs["card_moved"].id].status == "queued"
            item = db.exec(SqlBuilder.select.table(models.Checkitem).where(models.Checkitem.id == f.item.id)).first()
            assert item.is_checked is True
            card, count, visible_runs, visible_actions = committed(f)
            assert card[0] == f.source.id and count == 0 and visible_runs == visible_actions == []
            assert f.packets == []
            if rollback:
                raise RuntimeError("rollback cross-stage receipt")
            return result

    if rollback:
        with pytest.raises(RuntimeError, match="rollback cross-stage receipt"):
            move()
        card, count, runs, actions = committed(f)
        assert card[0] == f.source.id and card[2] == "active" and count == 0 and runs == actions == []
        with DbSession.use(readonly=False) as db:
            item = db.exec(SqlBuilder.select.table(models.Checkitem).where(models.Checkitem.id == f.item.id)).first()
            assert item.is_checked is False
        assert f.packets == []
        return
    assert move()["moved_to_review"] is True
    card, count, runs, actions = committed(f)
    assert card[0] == f.target.id and card[2] == "review" and count == 1
    assert len(runs) == len(actions) == 2
    assert any("to_column_uid" in packet for packet in f.packets)
    assert store(f)["created"] is False
    assert len(committed(f)[2]) == len(committed(f)[3]) == 2
    assert await run_due_rules(limit=2) == 1
    assert await run_due_rules(limit=2) == 0
    assert {run.status for run in committed(f)[2]} == {"succeeded"}
    assert {claim.status for claim in committed(f)[3]} == {"succeeded"}
