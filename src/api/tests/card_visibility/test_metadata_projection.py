"""Board metadata reads preserve visibility without loading complete editor rows."""

import os
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, CardMetadata, Organization, Project, ProjectColumn, User
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.MetadataService import MetadataService
from langboard_shared.helpers import InfraHelper
from langboard_shared.infrastructure.repositories.factory.MetadataRepository import MetadataRepository
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.orm import Session


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def metadata_db(request, monkeypatch):
    url = request.param
    if url == "postgresql-test":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    schema = None
    try:
        if engine.dialect.name == "postgresql":
            schema = "metadata_projection_" + uuid4().hex
            with engine.begin() as db:
                db.execute(text(f'CREATE SCHEMA "{schema}"'))

            @event.listens_for(engine, "begin")
            def use_schema(connection):
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')

        else:

            @event.listens_for(engine, "connect")
            def foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

        for model in (User, Organization, Project, ProjectColumn, Card, CardMetadata):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(
            DbEngine, "get_readonly_engine", lambda: pytest.fail("Metadata must use current primary rows")
        )
        with Session(engine) as db:
            for i in (1, 2):
                db.add(
                    User(
                        id=i, firstname="Metadata", lastname=str(i), email=f"m{i}@example.invalid", password="test-only"
                    )
                )
            db.flush()
            for i in (10, 20, 30):
                db.add(Project(id=i, owner_id=2, title="Metadata", deleted_at=SafeDateTime.now() if i == 30 else None))
            db.flush()
            for i, project_id, deleted in ((40, 10, False), (41, 10, True), (42, 20, False), (43, 30, False)):
                db.add(
                    ProjectColumn(
                        id=i, project_id=project_id, name="Metadata", deleted_at=SafeDateTime.now() if deleted else None
                    )
                )
            db.flush()
            for i, values in {
                101: {},
                102: {"visibility": "INTERNAL"},
                103: {"visibility": "PRIVATE", "owner_user_id": 1, "created_by_user_id": 1},
                104: {"visibility": "PRIVATE", "owner_user_id": 2, "created_by_user_id": 2},
                105: {"deleted_at": SafeDateTime.now()},
                106: {"source_type": "wiki", "source_uid": "linked-wiki"},
                108: {"project_column_id": 42},
                109: {"project_column_id": 41},
                110: {"project_id": 20, "project_column_id": 42},
                111: {},
                112: {"project_id": 30, "project_column_id": 43},
            }.items():
                data = {"project_id": 10, "project_column_id": 40, "visibility": "SHARED", **values}
                db.add(Card(id=i, title="Metadata", **data))
                db.flush()
                if i != 111:
                    db.add(CardMetadata(id=1000 + i * 2, card_id=i, key="note", value=str(i)))
                    db.add(
                        CardMetadata(id=1001 + i * 2, card_id=i, key=" INTERNAL.WORK_PLAN.receipt ", value="private")
                    )
            db.commit()
        yield engine
    finally:
        engine.dispose()
        if schema:
            cleanup = create_engine(url)
            try:
                with cleanup.begin() as db:
                    db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            finally:
                cleanup.dispose()


@pytest.mark.parametrize(
    "channel,internal,actor,expected",
    [
        (CollaborationChannel.HumanUI, True, 1, [101, 102, 103]),
        (CollaborationChannel.HumanUI, False, 1, [101, 103]),
        (CollaborationChannel.Api, True, 1, [101, 102]),
        (CollaborationChannel.Mcp, True, 1, [101, 102, 103]),
        (CollaborationChannel.HumanUI, True, 2, [101, 102, 104]),
    ],
)
def test_metadata_projection_keeps_current_visibility_and_private_receipts(
    metadata_db, channel, internal, actor, expected
):
    repo = MetadataRepository(None, None)
    context = CardVisibilityContext(channel, True, True, internal, actor_user_id=actor)
    card_service = SimpleNamespace(resolve_visibility_context=lambda *args: (10, context))
    service = MetadataService(None, lambda _: card_service, SimpleNamespace(metadata=repo))
    projections = []

    def capture(_connection, _cursor, _statement, _parameters, execution, _many):
        if execution.compiled is not None and execution.compiled.statement.is_select:
            projections.append([(c.table.name, c.name) for c in execution.compiled.statement.selected_columns])

    event.listen(metadata_db, "before_cursor_execute", capture)
    try:
        result = service.get_project_cards_as_api(10, object(), channel)
    finally:
        event.remove(metadata_db, "before_cursor_execute", capture)
    assert result == {InfraHelper.convert_uid(i): {"note": str(i)} for i in expected}
    assert projections == [[("card", "id"), ("card_metadata", "key"), ("card_metadata", "value")]]
    assert repo.get_by_project_cards(30, context=context) == []
    assert repo.get_by_project_cards(10, context=replace(context, active=False)) == []
    assert repo.get_by_project_cards(10, context=replace(context, project_member=False)) == []


def test_denied_context_never_reads_metadata():
    card_service = SimpleNamespace(resolve_visibility_context=lambda *args: None)
    service = MetadataService(None, lambda _: card_service, SimpleNamespace())
    assert service.get_project_cards_as_api(10, object()) == {}


def test_board_metadata_scope_excludes_old_archive_rows_in_sql(metadata_db, monkeypatch):
    now = SafeDateTime.now()
    cutoff = now - timedelta(days=3)
    monkeypatch.setattr(SafeDateTime, "now", staticmethod(lambda: now))
    with Session(metadata_db) as db:
        project = db.get(Project, 10)
        db.execute(update(Card).where(Card.id == 101).values(archived_at=cutoff - timedelta(microseconds=1)))
        db.execute(update(Card).where(Card.id == 102).values(archived_at=cutoff))
        db.add(CardMetadata(id=9000, card_id=111, key="note", value="recent"))
        db.execute(update(Card).where(Card.id == 111).values(archived_at=now))
        for index in range(100):
            db.add(Card(id=1000 + index, project_id=10, project_column_id=40, title="Old archive",
                        visibility="SHARED", archived_at=cutoff - timedelta(days=1)))
        db.flush()
        for index in range(100):
            db.add(CardMetadata(id=10000 + index, card_id=1000 + index, key="note", value="x" * 1000))
        db.commit()
        db.refresh(project)
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True, actor_user_id=1)
    card_service = SimpleNamespace(resolve_visibility_context=lambda *args: (project, context))
    service = MetadataService(None, lambda _: card_service, SimpleNamespace(metadata=MetadataRepository(None, None)))
    assert len(service.get_project_cards_as_api(project, object())) == 104
    selected_rows = []

    def capture(_connection, _cursor, statement, _parameters, context, _many):
        if context.compiled is not None and context.compiled.statement.is_select:
            selected_rows.append(statement)

    event.listen(metadata_db, "before_cursor_execute", capture)
    try:
        result = service.get_project_cards_as_api(project, object(), board_visible_only=True)
    finally:
        event.remove(metadata_db, "before_cursor_execute", capture)
    assert result == {InfraHelper.convert_uid(i): {"note": value} for i, value in ((102, "102"), (103, "103"), (111, "recent"))}
    assert len(selected_rows) == 1 and "archived_at" in selected_rows[0]
