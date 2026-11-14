"""Persisted native LINK, current source authority, provenance and safe projections."""

import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
    Card,
    CardAppOwnership,
    EmployeeMembershipPolicy,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.publishers import CardPublisher
from langboard_shared.tasks.activities import CardActivityTask
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.exc import IntegrityError
from src.api.tests.native_fixtures import TEST_PASSWORD_HASH


@pytest.fixture
def mirror_board(tmp_path, monkeypatch, request):
    engine = create_engine(f"sqlite:///{tmp_path / 'mirrors.db'}", connect_args={"check_same_thread": False})
    schema = None
    models = (User, EmployeeMembershipPolicy, Project, ProjectColumn, ProjectAssignedUser, ProjectRole, Card, CardAppOwnership, ProjectActivity,
              AttachmentStorageObject, AttachmentStorageReference)
    if getattr(request, "param", None) == "postgresql-test":
        database_url = os.environ.get("LANGBOARD_MIRROR_TEST_DATABASE_URL")
        if not database_url:
            pytest.skip("Set LANGBOARD_MIRROR_TEST_DATABASE_URL to a disposable PostgreSQL database")
        engine.dispose()
        engine = create_engine(database_url)
        schema = "card_mirror_test_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
        known = {model.__tablename__ for model in models}
        referenced = {fk.target_fullname.split(".")[0] for model in models for fk in model.__table__.foreign_keys}
        with engine.begin() as db:
            for name in sorted(referenced - known):
                db.execute(text(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)'))
    with engine.begin() as connection:
        # SQLite legacy mode does not start a transaction for DDL.
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        for model in models:
            model.__table__.create(connection)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    published = Mock()
    monkeypatch.setattr(CardPublisher, "created", published)
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Reader",
            lastname="One",
            email="one@example.invalid",
            password=TEST_PASSWORD_HASH,
            activated_at=SafeDateTime.now(),
        )
        outsider = User(
            firstname="Other",
            lastname="Owner",
            email="two@example.invalid",
            password=TEST_PASSWORD_HASH,
            activated_at=SafeDateTime.now(),
        )
        db.insert(actor)
        db.insert(outsider)
        source_board = Project(owner_id=outsider.id, title="Source secret board")
        destination = Project(owner_id=actor.id, title="Destination")
        db.insert(source_board)
        db.insert(destination)
        db.insert(ProjectAssignedUser(project_id=source_board.id, user_id=actor.id))
        db.insert(ProjectRole(project_id=source_board.id, user_id=actor.id, actions=["read"]))
        source_column = ProjectColumn(project_id=source_board.id, name="Source")
        column = ProjectColumn(project_id=destination.id, name="Target")
        db.insert(source_column)
        db.insert(column)
        source = Card(
            project_id=source_board.id,
            project_column_id=source_column.id,
            title="Secret source title",
            description=EditorContentModel(content="Original private body"),
            visibility="SHARED",
            created_by_user_id=outsider.id,
        )
        db.insert(source)
    service = CardService(
        lambda _: SimpleNamespace(is_employee=lambda _: False),
        lambda _: None,
        SimpleNamespace(card=CardRepository(None, None)),
    )
    try:
        yield SimpleNamespace(
            engine=engine,
            actor=actor,
            outsider=outsider,
            source_board=source_board,
            destination=destination,
            source=source,
            column=column,
            service=service,
            published=published,
        )
    finally:
        if schema:
            with engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def link(b, **kwargs):
    return b.service.link_cross_board_card(b.actor, b.source_board, b.source, b.destination, b.column, **kwargs)


def project(b, mirror, **kwargs):
    return b.service._get_linked_resource_payloads(b.actor, b.destination, [mirror], include_content=True, **kwargs)[
        mirror.get_uid()
    ]


def test_persisted_link_is_idempotent_with_exact_source_and_safe_socket(mirror_board):
    b = mirror_board
    mirror, payload, created = link(b)
    assert created is True
    again, _, created = link(b)
    assert again.id == mirror.id and created is False
    assert mirror.project_column_id == b.column.id
    assert mirror.source_type == "project_card" and mirror.source_uid == b.source.get_uid()
    assert mirror.title == "" and mirror.description.content == ""
    resource = project(b, mirror)
    assert resource["title"] == b.source.title
    assert resource["preview"] == b.source.description.content
    assert resource["url"] == f"/board/{b.source_board.get_uid()}/{b.source.get_uid()}"
    assert resource["allowed_actions"] == ["open_original", "remove_mirror"]
    with DbSession.use(readonly=False) as db:
        cards = db.exec(SqlBuilder.select.table(Card)).all()
        activities = db.exec(SqlBuilder.select.table(ProjectActivity)).all()
    assert len(cards) == 2 and len(activities) == 1
    activity = activities[0]
    assert activity.activity_type == ProjectActivityType.CardLinked
    assert activity.card_id == mirror.id
    assert activity.activity_history["source"]["uid"] == b.source.get_uid()
    assert b.source.title not in str(activity.activity_history)
    b.published.assert_called_once()
    broadcast = b.published.call_args.args[2]["card"]
    assert broadcast["title"] == ""
    assert broadcast["linked_resource"] == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}
    assert b.source.title not in str(broadcast) and b.source.description.content not in str(broadcast)


@pytest.mark.parametrize("revocation", ["role", "membership", "account", "deleted", "private", "app"])
def test_current_source_revocation_redacts_every_source_detail_and_denies_retry(mirror_board, revocation):
    b = mirror_board
    mirror, _, _ = link(b)
    with b.engine.begin() as db:
        if revocation == "role":
            db.execute(delete(ProjectRole).where(ProjectRole.project_id == b.source_board.id))
        elif revocation == "membership":
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id))
        elif revocation == "account":
            db.execute(update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif revocation == "deleted":
            db.execute(update(Card).where(Card.id == b.source.id).values(deleted_at=SafeDateTime.now()))
        elif revocation == "private":
            db.execute(
                update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id)
            )
    if revocation == "app":
        with DbSession.use(readonly=False) as db:
            db.insert(CardAppOwnership(card_id=b.source.id, app_key="test-app"))
    assert project(b, mirror) == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}
    assert link(b) is None


def test_projection_refreshes_source_content_without_copying_task_metadata(mirror_board):
    b = mirror_board
    mirror, _, _ = link(b)
    with b.engine.begin() as db:
        db.execute(update(Card).where(Card.id == b.source.id).values(title="Changed original"))
    resource = project(b, mirror)
    assert resource["title"] == "Changed original"
    assert not {"creator", "attachments", "labels", "member_uids", "relationships", "work_state"} & resource.keys()
    assert mirror.title == ""


def test_private_owner_requires_original_channel_authority(mirror_board):
    b = mirror_board
    with b.engine.begin() as db:
        db.execute(
            update(Card)
            .where(Card.id == b.source.id)
            .values(visibility="PRIVATE", owner_user_id=b.actor.id, created_by_user_id=b.actor.id)
        )
    assert link(b) is None
    mirror, _, _ = link(b, channel=CollaborationChannel.HumanUI)
    assert project(b, mirror)["status"] == "forbidden"
    assert project(b, mirror, channel=CollaborationChannel.HumanUI)["status"] == "available"


@pytest.mark.parametrize("invalid", ["wrong_source_board", "wrong_column", "archive", "destination_grant", "app"])
def test_rejects_invalid_current_scope_before_any_write(mirror_board, invalid):
    b = mirror_board
    source_board = b.source_board
    with b.engine.begin() as db:
        if invalid == "wrong_source_board":
            source_board = b.destination
        elif invalid == "wrong_column":
            db.execute(
                update(ProjectColumn).where(ProjectColumn.id == b.column.id).values(project_id=b.source_board.id)
            )
        elif invalid == "archive":
            db.execute(update(ProjectColumn).where(ProjectColumn.id == b.column.id).values(is_archive=True))
        elif invalid == "destination_grant":
            db.execute(update(Project).where(Project.id == b.destination.id).values(owner_id=b.outsider.id))
    if invalid == "destination_grant":
        with DbSession.use(readonly=False) as db:
            db.insert(ProjectAssignedUser(project_id=b.destination.id, user_id=b.actor.id))
            db.insert(ProjectRole(project_id=b.destination.id, user_id=b.actor.id, actions=["read"]))
    if invalid == "app":
        with DbSession.use(readonly=False) as db:
            db.insert(CardAppOwnership(card_id=b.source.id, app_key="test-app"))
    assert b.service.link_cross_board_card(b.actor, source_board, b.source, b.destination, b.column) is None
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(Card)).all()) == 1
        assert db.exec(SqlBuilder.select.table(ProjectActivity)).all() == []
    b.published.assert_not_called()


def test_activity_failure_rolls_back_reference_and_publication(mirror_board, monkeypatch):
    b = mirror_board
    monkeypatch.setattr(CardActivityTask, "record_card_linked", Mock(side_effect=RuntimeError("activity unavailable")))
    with pytest.raises(RuntimeError, match="activity unavailable"):
        link(b)
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(Card)).all()) == 1
        assert db.exec(SqlBuilder.select.table(ProjectActivity)).all() == []
    b.published.assert_not_called()


def test_native_unique_constraint_arbitrates_competing_reference_inserts(mirror_board):
    b = mirror_board

    def insert_reference(_):
        mirror = Card(
            project_id=b.destination.id,
            project_column_id=b.column.id,
            title="",
            source_type="project_card",
            source_uid=b.source.get_uid(),
        )
        try:
            with DbSession.use(readonly=False) as db:
                db.insert(mirror)
            return "created"
        except IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(insert_reference, range(2))) == ["created", "duplicate"]
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(Card).where(Card.source_type == "project_card")).all()) == 1


def test_native_remove_reference_preserves_original(mirror_board):
    b = mirror_board
    mirror, _, _ = link(b)
    b.service.repo.card.delete(mirror, purge=True)
    with DbSession.use(readonly=False) as db:
        original = db.exec(SqlBuilder.select.table(Card).where(Card.id == b.source.id)).first()
        assert original.title == b.source.title
        assert original.source_type is None
    assert link(b)[2] is True


def test_native_detail_reprojects_authority_and_rejects_mirror_writers(mirror_board, monkeypatch):
    b = mirror_board
    mirror, _, _ = link(b)
    monkeypatch.setattr(
        b.service,
        "get_work_states",
        lambda cards, **kwargs: {card.id: {"checklist_progress": {"total": 0, "completed": 0}} for card in cards},
    )
    assert b.service.get_details(b.destination, mirror, b.actor)["linked_resource"]["title"] == b.source.title
    assert b.service.update(b.actor, b.destination, mirror, {"title": "Changed"}) is None
    assert b.service.update_assigned_users(b.actor, b.destination, mirror, []) is None
    assert b.service.update_labels(b.actor, b.destination, mirror, []) is None
    assert b.service.cardify_selection(b.actor, b.destination, mirror, "injected") is None
    assert b.service.copy_selection_to_wiki(b.actor, b.destination, mirror, "injected") is None
    assert b.service.convert_description_checkboxes(b.actor, b.destination, mirror) is None
    assert b.service.set_card_completed(b.actor, b.destination, mirror, True) is None
    with pytest.raises(ValueError, match="read-only"):
        b.service.assign_self(b.actor, b.destination, mirror)
    with pytest.raises(ValueError, match="read-only"):
        CardService.assign_member.__wrapped__(b.service, b.actor, b.destination, mirror, b.actor.get_uid())
    assert b.service.change_order(b.actor, b.destination, mirror, 3, b.column) is None
    with b.engine.begin() as db:
        db.execute(delete(ProjectRole).where(ProjectRole.project_id == b.source_board.id))
    details = b.service.get_details(b.destination, mirror, b.actor)
    assert details["linked_resource"] == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}
    assert details["title"] == "" and details["count_comment"] == 0
    assert details["project_members"] == [] and details["relationships"] == []
    assert b.source.title not in str(details) and b.source.description.content not in str(details)


@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("create", ["paragraph", {"content": "injected"}], None),
        ("update", ["block", 1, {"content": "injected"}], None),
        ("delete", ["block"], None),
        ("move", ["block", None], None),
        ("replace_blocks", [[{"block_type": "paragraph", "payload": {"content": "injected"}}]], []),
    ],
)
def test_structured_content_writers_reject_card_mirrors(mirror_board, method, args, expected):
    from langboard_shared.domain.services.factory.CardContentBlockService import CardContentBlockService

    b = mirror_board
    mirror, _, _ = link(b)
    repo = Mock()
    service = CardContentBlockService(lambda _: None, lambda _: None, repo)
    assert getattr(service, method)(b.actor, b.destination, mirror, *args) == expected
    assert not repo.mock_calls


def test_api_rejects_unimplemented_clone_and_transfer(mirror_board):
    from fastapi import HTTPException
    from langboard.routes.board.BoardCardCrossBoardApi import CrossBoardCardForm, cross_board_card

    b = mirror_board
    for operation in ("clone", "transfer", "unsupported"):
        form = CrossBoardCardForm(
            source_project_uid=b.source_board.get_uid(),
            source_card_uid=b.source.get_uid(),
            destination_column_uid=b.column.get_uid(),
            operation=operation,
        )
        with pytest.raises(HTTPException):
            cross_board_card(
                b.destination.get_uid(), form, SimpleNamespace(scope={}), b.actor, SimpleNamespace(card=b.service)
            )


def test_api_link_returns_created_then_same_reference(mirror_board):
    import json
    from langboard.routes.board.BoardCardCrossBoardApi import CrossBoardCardForm, cross_board_card

    b = mirror_board
    form = CrossBoardCardForm(
        source_project_uid=b.source_board.get_uid(),
        source_card_uid=b.source.get_uid(),
        destination_column_uid=b.column.get_uid(),
    )
    first = cross_board_card(
        b.destination.get_uid(), form, SimpleNamespace(scope={}), b.actor, SimpleNamespace(card=b.service)
    )
    second = cross_board_card(
        b.destination.get_uid(), form, SimpleNamespace(scope={}), b.actor, SimpleNamespace(card=b.service)
    )
    assert first.status_code == 201 and second.status_code == 200
    assert json.loads(first.body)["card"]["uid"] == json.loads(second.body)["card"]["uid"]


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
def test_postgresql_concurrent_link_operations_return_one_card_and_one_event(mirror_board):
    b = mirror_board
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: link(b), range(2)))
    assert sorted(result[2] for result in results) == [False, True]
    assert results[0][0].id == results[1][0].id
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(Card).where(Card.source_type == "project_card")).all()) == 1
        activities = db.exec(SqlBuilder.select.table(ProjectActivity)).all()
        assert len(activities) == 1 and activities[0].activity_type == ProjectActivityType.CardLinked
    b.published.assert_called_once()


def test_current_destination_read_grant_is_required_for_source_projection(mirror_board):
    b = mirror_board
    mirror, _, _ = link(b)
    with b.engine.begin() as db:
        db.execute(update(Project).where(Project.id == b.destination.id).values(owner_id=b.outsider.id))
    with DbSession.use(readonly=False) as db:
        db.insert(ProjectAssignedUser(project_id=b.destination.id, user_id=b.actor.id))
        db.insert(ProjectRole(project_id=b.destination.id, user_id=b.actor.id, actions=["card_write"]))
    assert project(b, mirror) == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}
