"""Exercise movement, timer facts, idempotence and atomic failure against a real DB."""

import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from importlib import import_module
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
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    GlobalCardRelationshipType,
    GlobalLabel,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectExecutionBinding,
    ProjectLabel,
    User,
    WebhookSetting,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.CheckitemTimerRecordRepository import (
    CheckitemTimerRecordRepository,
)
from langboard_shared.publishers import CheckitemPublisher
from sqlalchemy import create_engine, event, select, text


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
            connection.execute(text("CREATE TABLE project(id BIGINT PRIMARY KEY)"))
            connection.execute(text("INSERT INTO project VALUES (1)"))
            connection.execute(text('CREATE TABLE "user"(id BIGINT PRIMARY KEY)'))
            connection.execute(text('INSERT INTO "user" VALUES (1)'))
    for model in (
        WorkflowStageDefinition,
        ProjectColumn,
        Card,
        CardAppOwnership,
        Checklist,
        Checkitem,
        CheckitemTimerRecord,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    project = Project(id=1, name="QA", title="QA")
    stage = WorkflowStageDefinition(
        key="released", name="Released", color="#64748B", entry_effects=["complete_checkitems"]
    )
    old = ProjectColumn(project_id=1, name="Active", workflow_stage=None)
    new = ProjectColumn(project_id=1, name="Released", workflow_stage="released")
    same = ProjectColumn(project_id=1, name="Same stage", workflow_stage="released")
    archive = ProjectColumn(project_id=1, name="Archive", is_archive=True, workflow_stage=None)
    with DbSession.atomic() as db:
        for row in (stage, old, new, same, archive):
            db.insert(row)
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
    notifications = []
    service.notify_order_changed = lambda *_: notifications.append(snapshot(engine, card.id))
    service.next_change_seq = lambda: 99
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
        actor=User(
            id=1,
            firstname="Test",
            lastname="Actor",
            email="actor@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        ),
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
            ProjectLabel,
            CardAssignedUser,
            CardAssignedProjectLabel,
        ):
            model.__table__.create(f.engine)
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
    assert f.notifications[0] == (column, items, timers)
    assert f.service.change_order(f.actor, f.project, f.card, 0, f.same) is True
    assert snapshot(f.engine, f.card.id)[2] == 4
    f.summary.assert_called_once()
    assert f.bulk.complete_unchecked_by_card(f.actor, f.project, f.card) == {"completed": 0, "stopped": 0}
    f.summary.assert_called_once()


def test_stop_only_keeps_unchecked_and_inactive_definition_applies(flow):
    f = flow
    with DbSession.atomic() as db:
        f.stage.entry_effects = ["stop_running_timers"]
        f.stage.is_active = False
        db.update(f.stage)
    f.service.change_order(f.actor, f.project, f.card, 0, f.new)
    _, items, timers = snapshot(f.engine, f.card.id)
    assert timers == 4 and not items[0][1] and not items[1][1] and not items[2][1]
    f.summary.assert_called_once()


def test_archive_and_reorder_do_not_apply_entry_effects(flow):
    f = flow
    f.service.change_order(f.actor, f.project, f.card, 0, None)
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
