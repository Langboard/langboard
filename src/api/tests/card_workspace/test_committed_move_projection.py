"""Committed notifications use real primary reads while ordinary reads may lag."""

import os
from contextlib import contextmanager
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard_shared.core.broadcast import DispatcherQueue
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    Card,
    CardChangeReceipt,
    CardDecisionGate,
    CardRelationship,
    CardVerificationRecord,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    Project,
    ProjectColumn,
    User,
    WorkflowRule,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.bases import BaseGraphApprovalRequestModel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.helpers import ModelHelper
from langboard_shared.infrastructure.repositories import Repository
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session


@pytest.fixture(params=["sqlite", "postgresql"])
def lagged_projection(request, monkeypatch, tmp_path):
    url = os.getenv("LANGBOARD_PROJECTION_TEST_DATABASE_URL")
    if request.param == "postgresql" and not url:
        pytest.skip("Set LANGBOARD_PROJECTION_TEST_DATABASE_URL to disposable PostgreSQL")
    engines, schemas = [], []
    admin = create_engine(url) if request.param == "postgresql" else None
    try:
        for name in ("primary", "replica"):
            if admin is not None:
                schema = f"committed_projection_{name}_{uuid4().hex}"
                with admin.begin() as db:
                    db.execute(text(f'CREATE SCHEMA "{schema}"'))
                schemas.append(schema)
                engine = create_engine(
                    url,
                    connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=3000"},
                )
            else:
                engine = create_engine(f"sqlite:///{tmp_path / (name + '.db')}")
            engines.append(engine)
        tables = set()

        def include(table):
            if table in tables:
                return
            tables.add(table)
            for foreign_key in table.foreign_keys:
                include(foreign_key.column.table)

        for model in (
            Card,
            CardRelationship,
            CardVerificationRecord,
            Checkitem,
            Checklist,
            CardChangeReceipt,
            CardDecisionGate,
            WorkflowRule,
            WorkflowStageDefinition,
            *ModelHelper.get_models_by_base_class(BaseGraphApprovalRequestModel),
        ):
            include(model.__table__)
        for engine in engines:
            Card.metadata.create_all(engine, tables=list(tables))
            with engine.begin() as db:
                db.execute(
                    text(
                        "CREATE TABLE card_execution_generation "
                        "(card_id BIGINT PRIMARY KEY, execution_generation BIGINT NOT NULL)"
                    )
                )
                db.execute(text("INSERT INTO card_execution_generation VALUES (100, 2)"))
                if engine.dialect.name == "postgresql":
                    db.execute(text("CREATE SEQUENCE content_change_seq START 6"))
            with Session(engine) as db:
                db.add(
                    User(
                        id=1,
                        firstname="Projection",
                        lastname="Reader",
                        email="reader@example.invalid",
                        password="fixture",
                    )
                )
                db.flush()
                db.add_all([Project(id=10, owner_id=1, title="Current"), Project(id=11, owner_id=1, title="Foreign")])
                db.flush()
                db.add_all(
                    [
                        ProjectColumn(id=40, project_id=10, name="Active", workflow_stage="active"),
                        ProjectColumn(id=41, project_id=10, name="Destination", workflow_stage="review"),
                        ProjectColumn(id=42, project_id=11, name="Foreign", workflow_stage="active"),
                        WorkflowStageDefinition(id=50, key="active", name="Active"),
                        WorkflowStageDefinition(id=51, key="review", name="Review"),
                        WorkflowStageDefinition(id=52, key="released", name="Released"),
                        GlobalCardRelationshipType(
                            id=70, parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks"
                        ),
                        GlobalCardRelationshipType(id=71, parent_name="Related", child_name="Related"),
                    ]
                )
                db.flush()
                db.add_all(
                    [
                        Card(
                            id=100 + index,
                            project_id=11 if index == 3 else 10,
                            project_column_id=42 if index == 3 else 40,
                            title=title,
                            visibility="SHARED",
                            last_change_seq=5,
                        )
                        for index, title in enumerate(
                            ("Moved", "Existing dependent", "New dependent", "Foreign", "Related")
                        )
                    ]
                )
                db.flush()
                db.add_all(
                    [
                        CardRelationship(id=80, card_id_parent=100, card_id_child=101, relationship_type_id=70),
                        CardRelationship(id=81, card_id_parent=100, card_id_child=103, relationship_type_id=70),
                        CardRelationship(id=82, card_id_parent=100, card_id_child=104, relationship_type_id=71),
                        Checklist(id=200, card_id=100, title="Acceptance"),
                        CardVerificationRecord(
                            id=300, card_id=100, source_change_seq=5, decision="unverified", recorded_by_user_id=1
                        ),
                    ]
                )
                db.flush()
                db.add(Checkitem(id=201, checklist_id=200, title="Required", is_checked=False))
                db.commit()
        primary, replica = engines
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: primary)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica)
        module = import_module(CardService.__module__)
        monkeypatch.setattr(module, "CardActivityTask", Mock())
        monkeypatch.setattr(module, "CardBotTask", Mock())

        # Readiness production has its own PostgreSQL-only contract. Keep the
        # native move, discovery, every projection query and socket serialization.
        @contextmanager
        def readiness():
            with DbSession.atomic() as db:
                yield SimpleNamespace(db=db, watch_card_and_dependents=lambda *_: None)

        monkeypatch.setattr(module, "execution_readiness_uow", readiness)
        repository, services = Repository(), {}

        def get(cls):
            if cls not in services:
                services[cls] = cls(get, None, repository)
            return services[cls]

        service = get(CardService)
        with DbSession.atomic() as db:
            actor = db.exec(SqlBuilder.select.table(User).where(User.id == 1)).first()
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 10)).first()
            card = db.exec(SqlBuilder.select.table(Card).where(Card.id == 100)).first()
            old = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == 40)).first()
            new = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == 41)).first()
        statements, emitted = [], []
        for name, engine in zip(("primary", "replica"), engines):
            event.listen(
                engine,
                "before_cursor_execute",
                lambda _c, _cursor, statement, *_, name=name: statements.append((name, statement)),
            )

        def transport(model):
            assert not DbSession.has_active_transaction(), "Projection transport escaped its read transaction"
            with primary.connect() as db:
                current = db.execute(select(Card.project_column_id, Card.last_change_seq).where(Card.id == 100)).one()
            assert current == (41, 6), "Notification escaped before the primary mutation committed"
            emitted.append(deepcopy(model.model_dump()["data"]))

        monkeypatch.setattr(DispatcherQueue, "put", transport)
        yield SimpleNamespace(
            primary=primary,
            replica=replica,
            service=service,
            actor=actor,
            project=project,
            card=card,
            old=old,
            new=new,
            emitted=emitted,
            statements=statements,
        )
    finally:
        for engine in engines:
            engine.dispose()
        if admin is not None:
            with admin.begin() as db:
                for schema in schemas:
                    db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def commit_move(fixture, *, rollback=False):
    """Exercise the native move and its after-commit callback without OAuth."""
    f = fixture
    with DbSession.atomic() as db:
        f.new.workflow_stage = "released"
        db.update(f.new)
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.id == 52)
        ).first()
        stage.counts_as_completed, stage.active_queue_policy = True, "exclude"
        db.update(stage)
        item = db.exec(SqlBuilder.select.table(Checkitem).where(Checkitem.id == 201)).first()
        item.is_checked = True
        db.update(item)
        db.insert(CardRelationship(card_id_parent=100, card_id_child=102, relationship_type_id=70))
        db.insert(CardVerificationRecord(card_id=100, source_change_seq=6, decision="verified", recorded_by_user_id=1))
        db.exec(text("UPDATE card_execution_generation SET execution_generation = 7 WHERE card_id = 100"))
        assert f.service._change_order(f.actor, f.project, f.card, 0, f.new)
        assert f.emitted == []
        f.statements.clear()
        if rollback:
            raise RuntimeError("rollback move")


def projections(fixture):
    return {
        packet["publish_models"][0]["card_uids"][0]: packet["data"]["work_state"]
        for packet in fixture.emitted
        if "work_state" in packet["data"]
    }


def assert_primary_projection(state):
    fields = (
        "workflow_stage",
        "completed",
        "active_queue_policy",
        "checklist_progress",
        "verification_state",
        "execution_generation",
    )
    assert {key: state[key] for key in fields} == {
        "workflow_stage": "released",
        "completed": True,
        "active_queue_policy": "exclude",
        "checklist_progress": {"total": 1, "completed": 1},
        "verification_state": "verified",
        "execution_generation": 7,
    }
    assert state["verification"]["source_change_seq"] == 6
    assert state["dependency_state"] == {"state": None, "direct_blockers": None, "scope": None}
    assert state["external_signal_evidence"] is None


def snapshot(engine):
    with engine.connect() as db:
        return {
            table.name: db.execute(select(table).order_by(*table.primary_key.columns)).all()
            for table in (
                Card.__table__,
                ProjectColumn.__table__,
                WorkflowStageDefinition.__table__,
                CardRelationship.__table__,
                Checklist.__table__,
                Checkitem.__table__,
                CardVerificationRecord.__table__,
            )
        }


def test_committed_move_discovers_new_primary_dependents(lagged_projection):
    f = lagged_projection
    commit_move(f)
    assert set(projections(f)) == {SnowflakeID(key).to_short_code() for key in (100, 101, 102)}
    order = next(index for index, packet in enumerate(f.emitted) if "to_column_uid" in packet["data"])
    state = next(index for index, packet in enumerate(f.emitted) if "work_state" in packet["data"])
    assert order < state, "Order event must precede projections"
    assert f.emitted[order]["data"]["to_column_uid"] == f.new.get_uid()
    assert not any(name == "replica" for name, _ in f.statements)


def test_committed_move_emits_current_policy_checklist_and_verification(lagged_projection):
    f = lagged_projection
    commit_move(f)
    assert_primary_projection(projections(f)[f.card.get_uid()])
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for _, sql in f.statements)


def test_standalone_publish_uses_primary_without_writes_or_changing_read_routing(lagged_projection):
    f = lagged_projection
    commit_move(f)
    f.emitted.clear()
    before = snapshot(f.primary)
    f.statements.clear()
    f.service.publish_work_states(f.project, [f.card.id, f.card.id, 103, 999])
    assert set(projections(f)) == {f.card.get_uid()}, "Projection must retain the supplied project scope"
    assert_primary_projection(projections(f)[f.card.get_uid()])
    assert not any(name == "replica" for name, _ in f.statements)
    assert snapshot(f.primary) == before, "Projection must not change timestamps, receipts or native facts"
    f.statements.clear()
    # The public repository and ordinary projector retain their replica routing.
    children = f.service.repo.card_relationship.get_all_by_card_and_relation(f.card, relation="child")
    assert {child.id for _, _, child in children} == {101, 103, 104}
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first()
    state = f.service.get_work_states([current])[current.id]
    assert state["workflow_stage"] == "review" and state["completed"] is False
    assert state["checklist_progress"] == {"total": 1, "completed": 0}
    assert state["verification_state"] == "stale"
    assert any(name == "replica" for name, _ in f.statements)


def test_caught_up_replica_keeps_normal_notifications_and_projection_read_only(lagged_projection):
    f = lagged_projection
    commit_move(f)
    # Catch up the actual related rows; card discovery endpoints themselves did
    # not change. This is the nearby normal case for the same native notifier.
    tables = [
        model.__table__
        for model in (
            WorkflowStageDefinition,
            ProjectColumn,
            Checklist,
            Checkitem,
            CardRelationship,
            CardVerificationRecord,
        )
    ]
    with f.primary.connect() as source, f.replica.begin() as target:
        for table in tables:
            rows = source.execute(select(table)).mappings().all()
            for row in rows:
                values = dict(row)
                if not target.execute(table.update().where(table.c.id == row["id"]).values(**values)).rowcount:
                    target.execute(table.insert(), values)
    before = snapshot(f.primary), snapshot(f.replica)
    f.emitted.clear()
    f.service.notify_order_changed(f.actor, f.project, f.card, f.old, f.new)
    assert set(projections(f)) == {SnowflakeID(key).to_short_code() for key in (100, 101, 102)}
    assert_primary_projection(projections(f)[f.card.get_uid()])
    assert (snapshot(f.primary), snapshot(f.replica)) == before


def test_rolled_back_move_discards_native_callback(lagged_projection):
    f = lagged_projection
    before = snapshot(f.primary)
    with pytest.raises(RuntimeError, match="rollback move"):
        commit_move(f, rollback=True)
    assert f.emitted == []
    assert snapshot(f.primary) == before


def test_projection_transport_failure_keeps_committed_move(lagged_projection, monkeypatch, caplog):
    f = lagged_projection
    attempted = []

    def failed_transport(model):
        packet = model.model_dump()["data"]["data"]
        assert not DbSession.has_active_transaction()
        attempted.append(packet)
        if "work_state" in packet:
            raise RuntimeError("projection transport unavailable")

    monkeypatch.setattr(DispatcherQueue, "put", failed_transport)
    commit_move(f)
    with f.primary.connect() as db:
        assert db.execute(select(Card.project_column_id, Card.last_change_seq).where(Card.id == 100)).one() == (41, 6)
    assert any("to_column_uid" in packet for packet in attempted)
    assert any("work_state" in packet for packet in attempted)
    assert "After-commit callback failed: RuntimeError" in caplog.text
