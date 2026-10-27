"""Exercise movement, timer facts, idempotence and atomic failure against a real DB."""

import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from importlib import import_module
from itertools import count
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAppOwnership,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardChangeReceipt,
    CardDecisionGate,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    DecisionCase,
    GlobalCardRelationshipType,
    GlobalLabel,
    Organization,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectExecutionBinding,
    ProjectLabel,
    ProjectRole,
    User,
    WebhookSetting,
    WorkflowActionRun,
    WorkflowEffectRun,
    WorkflowRule,
    WorkflowRuleAction,
    WorkflowRuleCondition,
    WorkflowRuleRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.domain.services.factory.NotificationService import NotificationService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.CheckitemTimerRecordRepository import (
    CheckitemTimerRecordRepository,
)
from langboard_shared.publishers import CheckitemPublisher
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session


def set_entry_actions(stage, kinds, *, conditions=None):
    """Persist public normalized definitions using the real rule repository path."""
    from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules

    rules = [] if not kinds else [{
        "name": "Native entry safety", "is_enabled": True,
        "trigger": {"kind": "stage_entered"}, "condition_match": "all",
        "conditions": conditions or [],
        "actions": [{"kind": kind, "executor": {"kind": "event_actor"},
                     "config": {}, "requires_previous_success": False} for kind in kinds],
    }]
    with DbSession.atomic() as db:
        replace_rules(db, stage, rules)


@pytest.fixture
def flow(monkeypatch, request):
    schema = None
    database_url = os.getenv("LANGBOARD_EFFECT_TEST_DATABASE", "sqlite://")
    engine = create_engine(database_url)
    if "bouncer" in (engine.url.host or "").lower():
        engine.dispose()
        raise ValueError("Workflow schema tests require a direct PostgreSQL connection, not PgBouncer")

    def cleanup():
        engine.dispose()
        if schema is not None:
            cleanup_engine = create_engine(engine.url)
            try:
                with cleanup_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            finally:
                cleanup_engine.dispose()

    request.addfinalizer(cleanup)
    if engine.dialect.name == "postgresql":
        schema = "uow236_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        @event.listens_for(engine, "begin")
        def set_schema(connection):
            # Transaction-local scope cannot leak through a database connection pool.
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')

        engine.dispose()
    with engine.begin() as connection:
        for model in (
            User, Organization, Project, ProjectAssignedUser, ProjectRole,
            WorkflowRule, WorkflowRuleAction, WorkflowRuleCondition, WorkflowRuleRun, WorkflowActionRun,
            WorkflowStageDefinition,
            WorkflowEffectRun,
            ProjectColumn,
            Card,
            CardChangeReceipt,
            CardAppOwnership,
            DecisionCase,
            CardDecisionGate,
            Checklist,
            Checkitem,
            CheckitemTimerRecord,
        ):
            model.__table__.create(connection)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    project = Project(id=1, owner_id=1, title="QA")
    actor = User(id=1, firstname="Test", lastname="Actor", email="actor@example.invalid",
                 password="test-only", activated_at=SafeDateTime.now())
    stage = WorkflowStageDefinition(
        key="released", name="Released", color="#64748B"
    )
    old = ProjectColumn(project_id=1, name="Active", workflow_stage=None)
    new = ProjectColumn(project_id=1, name="Released", workflow_stage="released")
    same = ProjectColumn(project_id=1, name="Same stage", workflow_stage="released")
    archive = ProjectColumn(project_id=1, name="Archive", is_archive=True, workflow_stage=None)
    with Session(engine, expire_on_commit=False) as authority:
        authority.add(actor)
        authority.flush()
        authority.add(project)
        authority.commit()
    with DbSession.atomic() as db:
        for row in (stage, old, new, same, archive):
            db.insert(row)
        set_entry_actions(stage, ["checkitems.complete"])
        card = Card(project_id=1, project_column_id=old.id, title="QA")
        db.insert(card)
        checklist = Checklist(card_id=card.id, title="QA")
        db.insert(checklist)
        items = [
            Checkitem(
                checklist_id=checklist.id,
                title=str(i),
                user_id=1 if i < 2 else None,
                status=[CheckitemStatus.Started, CheckitemStatus.Paused, CheckitemStatus.Stopped][min(i, 2)],
                accumulated_seconds=9,
                is_checked=i == 3,
                cardified_id=card.id if i == 4 else None,
            )
            for i in range(5)
        ]
        for row in items:
            db.insert(row)
        for row in items[:2]:
            db.insert(
                CheckitemTimerRecord(
                    checkitem_id=row.id, status=row.status, created_at=SafeDateTime.now() - timedelta(seconds=120)
                )
            )
    repo = SimpleNamespace(
        card=CardRepository(None, None), checkitem_timer_record=CheckitemTimerRecordRepository(None, None)
    )
    services = {}

    def get(cls):
        if cls not in services:
            services[cls] = cls(get, None, repo)
        return services[cls]

    service = get(CardService)
    # Load the worker before replacing the package export; otherwise its cached
    # import retains this fixture's service after monkeypatch restores the export.
    worker = import_module("langboard_shared.tasks.WorkflowRuleTask")
    def domain_service():
        return SimpleNamespace(card=service, checkitem=get(CheckitemService), notification=get(NotificationService))

    monkeypatch.setattr("langboard_shared.domain.services.DomainService",
                        domain_service)
    monkeypatch.setattr(worker, "DomainService", domain_service)
    notifications = []
    service.notify_order_changed = lambda *_: notifications.append(snapshot(engine, card.id))
    change_sequences = count(99)
    service.next_change_seq = lambda: next(change_sequences)
    summary = Mock()
    monkeypatch.setattr(CheckitemPublisher, "workflow_effects_applied", summary)

    @contextmanager
    def readiness():
        with DbSession.atomic() as db:
            yield SimpleNamespace(db=db, watch_card_and_dependents=lambda *_: None)

    monkeypatch.setattr(import_module(CardService.__module__), "execution_readiness_uow", readiness)
    yield SimpleNamespace(
        engine=engine,
        service=service,
        bulk=get(CheckitemService),
        project=project,
        actor=actor,
        card=card,
        old=old,
        new=new,
        same=same,
        archive=archive,
        stage=stage,
        items=items,
        summary=summary,
        notifications=notifications,
    )


def snapshot(engine, card_id):
    with engine.connect() as db:
        column = db.execute(
            select(Card.__table__.c.project_column_id).where(Card.__table__.c.id == card_id)
        ).scalar_one()
        items = db.execute(
            select(
                Checkitem.__table__.c.id,
                Checkitem.__table__.c.is_checked,
                Checkitem.__table__.c.status,
                Checkitem.__table__.c.accumulated_seconds,
            ).order_by(Checkitem.__table__.c.title)
        ).all()
        timers = db.execute(select(CheckitemTimerRecord.__table__.c.id)).all()
        return column, items, len(timers)


@pytest.mark.parametrize("native_readiness", [False, True])
def test_concurrent_card_reorders_do_not_deadlock(flow, monkeypatch, native_readiness):
    """Hold the first card lock while a second move reaches a real lock wait."""
    f = flow
    if f.engine.dialect.name != "postgresql":
        pytest.skip("Concurrent row locks require PostgreSQL")
    enqueued = []
    if native_readiness:
        from langboard_shared.tasks.webhooks import ExecutionOutboxTask
        from langboard_shared.tasks.webhooks.ExecutionReadinessUow import execution_readiness_uow

        for model in (
            WebhookSetting,
            GlobalCardRelationshipType,
            ProjectAssignedUser,
            GlobalLabel,
    Organization,
            ProjectLabel,
            CardAssignedUser,
            CardAssignedProjectLabel,
        ):
            model.__table__.create(f.engine, checkfirst=True)
        from langboard_shared.domain.models import CardRelationship

        CardRelationship.__table__.create(f.engine)
        path = Path(__file__).parents[2] / "langboard/migrations/versions/20260924220000-7ad15b1d0b70.py"
        spec = importlib.util.spec_from_file_location("reorder_execution_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with f.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE project_execution_binding (id BIGINT PRIMARY KEY, created_at timestamptz, "
                    "updated_at timestamptz, project_id BIGINT UNIQUE, is_enabled BOOLEAN, column_semantics JSON, "
                    "prerequisite_relationship_type_uid TEXT, webhook_uid TEXT, events JSON)"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        with DbSession.atomic() as db:
            hook = WebhookSetting(
                name="Test",
                url="https://example.invalid/events",
                secret_id="test-only",
                events=["io.langboard.work.ready.v1"],
            )
            relation = GlobalCardRelationshipType(
                parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks"
            )
            db.insert(hook)
            db.insert(relation)
            db.insert(
                ProjectExecutionBinding(
                    project_id=f.project.id,
                    is_enabled=True,
                    column_semantic_ids={str(f.new.id): "ready"},
                    prerequisite_relationship_type_id=relation.id,
                    webhook_id=hook.id,
                    webhook_uid=hook.get_uid(),
                    events=["io.langboard.work.ready.v1"],
                )
            )
        monkeypatch.setattr(import_module(CardService.__module__), "execution_readiness_uow", execution_readiness_uow)

        def enqueue(event_id):
            # A separate connection must see the event before external dispatch.
            with f.engine.connect() as db:
                assert (
                    db.execute(
                        text("SELECT state FROM execution_outbox WHERE id = :event_id"), {"event_id": event_id}
                    ).scalar_one()
                    == "pending"
                )
            enqueued.append(event_id)

        monkeypatch.setattr(ExecutionOutboxTask, "execution_outbox_task", enqueue)
    target = f.new if native_readiness else None
    with DbSession.atomic() as db:
        second = Card(project_id=1, project_column_id=f.old.id, title="Second", order=1)
        third = Card(project_id=1, project_column_id=f.old.id, title="Third", order=2)
        db.insert(second)
        db.insert(third)
    locked, release = Event(), Event()

    def hold_first_card(connection, cursor, statement, parameters, context, executemany):
        if "FROM card" in statement and "FOR UPDATE" in statement and f.card.id in parameters.values():
            locked.set()
            assert release.wait(15), "First move was not released"

    event.listen(f.engine, "after_cursor_execute", hold_first_card)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                f.service.change_order, f.actor, f.project, f.card, 0 if native_readiness else 2, target
            )
            assert locked.wait(10), "First move did not lock its card"
            following = pool.submit(f.service.change_order, f.actor, f.project, second, 0, target)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with f.engine.connect() as db:
                        waiting = db.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND pid<>pg_backend_pid()"
                            )
                        ).scalar_one()
                    if waiting:
                        break
                    if following.done():
                        pytest.fail("Second move did not wait for the first transaction")
                    time.sleep(0.02)
                else:
                    pytest.fail("No PostgreSQL lock waiter observed")
            finally:
                release.set()
            assert first.result(timeout=10) is True
            assert following.result(timeout=10) is True
    finally:
        release.set()
        event.remove(f.engine, "after_cursor_execute", hold_first_card)
    with f.engine.connect() as db:
        rows = db.execute(select(Card.id, Card.order).order_by(Card.order)).all()
    if not native_readiness:
        assert rows == [(second.id, 0), (third.id, 1), (f.card.id, 2)]
    else:
        with f.engine.connect() as db:
            assert db.execute(
                select(Card.id, Card.order).where(Card.project_column_id == f.new.id).order_by(Card.order)
            ).all() == [(second.id, 0), (f.card.id, 1)]
            assert db.execute(select(Card.id, Card.order).where(Card.project_column_id == f.old.id)).all() == [
                (third.id, 0)
            ]
        assert f.service.change_order(f.actor, f.project, f.card, 0, f.new) is True
        with f.engine.connect() as db:
            outbox = db.execute(
                text("SELECT card_id, execution_generation, state FROM execution_outbox ORDER BY card_id")
            ).all()
            counters = db.execute(
                text("SELECT card_id, execution_generation FROM card_execution_generation ORDER BY card_id")
            ).all()
            assert db.execute(
                select(Card.project_column_id).where(Card.id.in_([f.card.id, second.id]))
            ).scalars().all() == [f.new.id, f.new.id]
            assert db.execute(
                select(Card.id, Card.order).where(Card.project_column_id == f.new.id).order_by(Card.order)
            ).all() == [(f.card.id, 0), (second.id, 1)]
        expected = sorted([f.card.id, second.id])
        assert outbox == [(card_id, 1, "pending") for card_id in expected]
        assert counters == [(card_id, 1) for card_id in expected]
        assert len(enqueued) == 2 and len(set(enqueued)) == 2


def test_column_rebinding_invalidates_residency_without_reapplying_effects(flow, monkeypatch):
    from langboard_shared.domain.services.factory.ProjectColumnService import ProjectColumnService
    from langboard_shared.infrastructure.repositories.factory.ProjectColumnRepository import ProjectColumnRepository
    from langboard_shared.infrastructure.repositories.factory.WorkflowStageRepository import WorkflowStageRepository

    f = flow
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)

    @contextmanager
    def readiness():
        with DbSession.atomic() as db:
            yield SimpleNamespace(db=db, before={}, watch_column_and_dependents=lambda *_: None)

    monkeypatch.setattr(import_module(ProjectColumnService.__module__), "execution_readiness_uow", readiness)
    monkeypatch.setattr("langboard_shared.publishers.ProjectColumnPublisher.workflow_stage_changed", lambda *_: None)
    columns = ProjectColumnService(lambda _: SimpleNamespace(publish_work_states=lambda *_: None), None,
                                   SimpleNamespace(project_column=ProjectColumnRepository(None, None),
                                                   workflow_stage=WorkflowStageRepository(None, None)))
    assert columns.change_workflow_stage(f.project, f.new, None)
    with f.engine.connect() as db:
        assert db.execute(select(Card.__table__.c.workflow_stage_entered_key,
                                 Card.__table__.c.workflow_stage_entered_at)
                          .where(Card.__table__.c.id == f.card.id)).one() == (None, None)


def test_stage_residency_tracks_observed_entries_without_backfill(flow):
    f = flow

    def residency():
        with f.engine.connect() as db:
            return db.execute(select(Card.__table__.c.workflow_stage_entered_key,
                                     Card.__table__.c.workflow_stage_entered_at)
                              .where(Card.__table__.c.id == f.card.id)).one()

    assert residency() == (None, None)
    with DbSession.atomic() as db:
        set_entry_actions(f.stage, [])
        db.update(f.stage)
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    first = residency()
    assert first[0] == "released" and first[1] is not None
    # A same-stage column move or reorder does not restart the stage clock.
    f.service.change_order(f.actor, f.project, f.card, 0, f.same)
    assert residency() == first
    f.service.change_order(f.actor, f.project, f.card, 0, None)
    assert residency() == first
    f.service.change_order(f.actor, f.project, f.card, 0, f.old)
    assert residency() == (None, None)
    with pytest.raises(RuntimeError, match="rollback"), DbSession.atomic():
        f.service.change_order(f.actor, f.project, f.card, 0, f.new)
        raise RuntimeError("rollback")
    assert residency() == (None, None)
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    assert residency()[0] == "released"
    f.service.change_order(f.actor, f.project, f.card, 0, f.archive)
    assert residency() == (None, None)


def test_move_completes_once_stops_started_and_paused_preserves_cardified(flow):
    f = flow
    assert f.service.change_order(f.actor, f.project, f.card, 0, f.new) is True
    column, items, timers = snapshot(f.engine, f.card.id)
    assert column == f.new.id and timers == 4
    assert all(row[1] for row in items[:4])
    assert all(row[2] == CheckitemStatus.Stopped for row in items[:4])
    assert items[0][3] >= 129 and items[1][3] == 9
    assert not items[4][1] and items[4][3] == 9
    f.summary.assert_called_once()
    assert len(f.summary.call_args.args[2]) == 3
    with DbSession.use(readonly=False) as db:
        runs = db.exec(SqlBuilder.select.table(WorkflowRuleRun)).all()
        assert len(runs) == 1
        run = runs[0]
        assert (run.stage_id, run.stage_revision) == (f.stage.id, f.stage.edit_revision())
        assert run.rule_revision == 1 and run.event_type == "stage_entered"
        assert (run.project_id, run.card_id, run.actor_id, run.actor_type) == (1, f.card.id, 1, "user")
        actions = db.exec(SqlBuilder.select.table(WorkflowActionRun).where(WorkflowActionRun.rule_run_id == run.id)).all()
        assert len(actions) == 1 and actions[0].kind == "checkitems.complete"
        assert actions[0].status == "succeeded"
        assert run.status == "succeeded"
        assert actions[0].result == {"completed": 3, "stopped": 2}
        assert run.finished_at >= run.occurred_at
        assert actions[0].finished_at >= actions[0].started_at
    assert f.notifications[0] == (column, items, timers)
    assert f.service.change_order(f.actor, f.project, f.card, 0, f.same) is True
    assert snapshot(f.engine, f.card.id)[2] == 4
    f.summary.assert_called_once()
    assert f.bulk.complete_unchecked_by_card(f.actor, f.project, f.card) == {"completed": 0, "stopped": 0}
    f.summary.assert_called_once()
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(WorkflowRuleRun)).all()) == 1


def test_stop_only_keeps_unchecked_and_inactive_definition_applies(flow):
    f = flow
    with DbSession.atomic() as db:
        set_entry_actions(f.stage, ["timers.stop"])
        f.stage.is_active = False
        db.update(f.stage)
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    _, items, timers = snapshot(f.engine, f.card.id)
    assert timers == 4 and not items[0][1] and not items[1][1] and not items[2][1]
    f.summary.assert_called_once()


@pytest.mark.parametrize("reorder_parent", [None, ""])
def test_archive_and_reorder_do_not_apply_entry_effects(flow, reorder_parent):
    f = flow
    f.service.change_order(f.actor, f.project, f.card, 0, reorder_parent)
    assert snapshot(f.engine, f.card.id)[2] == 2
    f.service.change_order(f.actor, f.project, f.card, 0, f.archive)
    assert snapshot(f.engine, f.card.id)[2] == 2
    f.summary.assert_not_called()


def test_missing_timer_fact_rolls_back_movement_and_every_item(flow):
    f = flow
    with f.engine.begin() as db:
        db.execute(CheckitemTimerRecord.__table__.delete())
    before = snapshot(f.engine, f.card.id)
    with pytest.raises(ValueError, match="matching timer fact"):
        f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    assert snapshot(f.engine, f.card.id) == before
    f.summary.assert_not_called()
    assert f.notifications == []
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(WorkflowRuleRun)).all() == []


def test_outer_rollback_suppresses_summary_and_movement_notifications(flow):
    f = flow
    before = snapshot(f.engine, f.card.id)
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            f.service.change_order(f.actor, f.project, f.card, 0, f.new)
            f.summary.assert_not_called()
            assert f.notifications == []
            raise RuntimeError("rollback")
    assert snapshot(f.engine, f.card.id) == before
    f.summary.assert_not_called()
    assert f.notifications == []
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(WorkflowRuleRun)).all() == []


def test_same_stage_does_not_complete_new_unchecked_item(flow):
    f = flow
    with DbSession.atomic() as db:
        f.old.workflow_stage = "released"
        db.update(f.old)
    before = snapshot(f.engine, f.card.id)
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    assert snapshot(f.engine, f.card.id)[1:] == before[1:]
    f.summary.assert_not_called()


def test_deleted_and_cardified_running_items_are_preserved(flow):
    f = flow
    with DbSession.atomic() as db:
        f.items[0].cardified_id = f.card.id
        f.items[1].deleted_at = SafeDateTime.now()
        db.update(f.items[0])
        db.update(f.items[1])
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    _, items, timers = snapshot(f.engine, f.card.id)
    assert timers == 2
    assert items[0][2] == CheckitemStatus.Started and not items[0][1]
    assert items[1][2] == CheckitemStatus.Paused and not items[1][1]
    assert items[2][1] and items[3][1] and not items[4][1]
    f.summary.assert_called_once()


def test_retry_same_column_does_not_complete_new_unchecked_item(flow):
    f = flow
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    with DbSession.atomic() as db:
        # The fixture's detached item predates the first transition.
        item = db.exec(SqlBuilder.select.table(Checkitem).where(Checkitem.column("id") == f.items[2].id)).first()
        assert item.is_checked is True
        item.is_checked = False
        db.update(item)
    assert snapshot(f.engine, f.card.id)[1][2][1] is False
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    assert snapshot(f.engine, f.card.id)[1][2][1] is False
    f.summary.assert_called_once()


def test_system_completion_checkbox_is_kept_in_sync(flow):
    f = flow
    with DbSession.atomic() as db:
        checklist = Checklist(card_id=f.card.id, title="", is_system=True)
        db.insert(checklist)
        db.insert(Checkitem(checklist_id=checklist.id, title="QA"))
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    with f.engine.connect() as db:
        assert (
            db.execute(
                select(Checklist.__table__.c.is_checked).where(Checklist.__table__.c.id == checklist.id)
            ).scalar_one()
            is True
        )
    f.summary.assert_called_once()


def test_large_checklist_emits_one_summary_and_batches_timer_reads(flow):
    f = flow
    with DbSession.atomic() as db:
        for i in range(1000):
            db.insert(Checkitem(checklist_id=f.items[0].checklist_id, title=f"bulk-{i}"))
    queries = []
    f.service.notify_order_changed = lambda *_: None
    event.listen(f.engine, "before_cursor_execute", lambda _c, _cursor, statement, *_: queries.append(statement))
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    f.summary.assert_called_once()
    assert len(f.summary.call_args.args[2]) == 1003
    timer_reads = [
        query for query in queries if query.lstrip().upper().startswith("SELECT") and "checkitem_timer_record" in query
    ]
    assert len(timer_reads) <= 3
    _, items, timers = snapshot(f.engine, f.card.id)
    assert sum(row[1] for row in items) == 1004 and timers == 4


@pytest.mark.parametrize("case", ["other", "self", "no_author", "disabled", "same_stage", "denied", "unsubscribed", "rollback", "retry", "reorder"])
def test_author_entry_notification_is_atomic_and_respects_recipient_settings(flow, monkeypatch, case):
    from langboard_shared.domain.models import UserNotification
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    from langboard_shared.infrastructure.repositories.factory.UserNotificationRepository import (
        UserNotificationRepository,
    )

    f = flow
    UserNotification.__table__.create(f.engine)
    notification_service = f.service._get_service(NotificationService)
    notification_service.repo.user_notification = UserNotificationRepository(None, None)
    settings = Mock()
    settings.has_unsubscription.return_value = case == "unsubscribed"
    monkeypatch.setattr(notification_service, "_get_service", lambda *_: settings)
    recipient = f.actor
    monkeypatch.setattr(notification_service, "_resolve_notification_recipient", lambda user, kind, refs:
                        None if case == "denied" else (recipient, refs))
    fanout = Mock()
    work = Mock()
    monkeypatch.setattr(notification_service, "_publish_web_notification", fanout)
    monkeypatch.setattr(notification_service, "convert_to_api_response", lambda *_: {})
    monkeypatch.setattr(import_module(NotificationService.__module__), "publish_pending_work_events", work)
    with DbSession.atomic() as db:
        set_entry_actions(f.stage, [] if case == "disabled" else ["author.notify"],
                          conditions=[{"kind": "actor_is_not_author", "operator": "eq", "value": True}])
        f.card.created_by_user_id = None if case == "no_author" else recipient.id
        db.update(f.stage)
        db.update(f.card)
        if case == "same_stage":
            f.old.workflow_stage = f.new.workflow_stage
            db.update(f.old)
    actor = recipient if case == "self" else User(
        id=2, firstname="Other", lastname="Actor", email="other-actor@example.invalid",
        password="test-only", activated_at=SafeDateTime.now())
    if actor.id != recipient.id:
        actor.email = "other-actor@example.invalid"
        with Session(f.engine, expire_on_commit=False) as authority:
            authority.add(actor)
            authority.commit()
        with DbSession.atomic() as db:
            db.insert(ProjectAssignedUser(project_id=f.project.id, user_id=actor.id))
            db.insert(ProjectRole(project_id=f.project.id, user_id=actor.id, actions=["read", "card_update"]))
    stale_card = f.card.model_copy(deep=True)
    def move():
        with DbSession.atomic():
            assert f.service.change_order(actor, f.project, f.card, 0, f.new) is True
            fanout.assert_not_called()
            work.assert_not_called()
            if case == "rollback":
                raise RuntimeError("rollback notification")
    if case == "rollback":
        with pytest.raises(RuntimeError, match="rollback notification"):
            move()
    else:
        move()
    if case in {"retry", "reorder"}:
        # A request replay may carry the detached card from before entry.
        assert f.service.change_order(actor, f.project, stale_card, 0, f.new if case == "retry" else None) is True
    expected = case in {"other", "unsubscribed", "retry", "reorder"}
    with DbSession.use(readonly=False) as db:
        notes = db.exec(SqlBuilder.select.table(UserNotification)).all()
        assert len(notes) == int(expected)
        if expected:
            note = notes[0]
            assert note.notification_type == NotificationType.WorkflowStageEntered
            assert note.receiver_id == recipient.id and note.notifier_id == actor.id
            assert note.message_vars == {"stage_key": "released", "column_name": "Released"}
            assert note.web_visible is (case != "unsubscribed")
            assert note in notification_service.repo.user_notification.get_pending_work_events()
            runs = db.exec(SqlBuilder.select.table(WorkflowRuleRun)).all()
            assert len(runs) == 1
            claims = db.exec(SqlBuilder.select.table(WorkflowActionRun).where(WorkflowActionRun.rule_run_id == runs[0].id)).all()
            assert len(claims) == 1 and claims[0].kind == "author.notify"
            assert claims[0].result == {"notified": 1} and claims[0].status == "succeeded"
    assert fanout.call_count == int(case in {"other", "retry", "reorder"})
    assert work.call_count == int(expected)
    # A notify-only effect must never finish checkitems or stop timers.
    assert snapshot(f.engine, f.card.id)[2] == 2
    f.summary.assert_not_called()
