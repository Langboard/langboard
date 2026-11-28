"""Real database acceptance for the human-only discussion boundary."""

from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    CardComment,
    CardContentBlock,
    CardRelationship,
    Checklist,
    GlobalCardRelationshipType,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    User,
    WhisperMessage,
    WhisperParticipant,
    WhisperThread,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.WhisperThreads import (
    WhisperUnavailable,
    create_thread,
    list_participants,
    list_threads,
    mark_read,
    read_messages,
    reply,
    set_state,
)
from langboard_shared.Env import Env
from sqlalchemy import func


pytestmark = pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)

HUMAN = CollaborationChannel.HumanUI


@pytest.fixture
def discussion(current_card, monkeypatch):
    user, project, card, card_service = current_card
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    for model in (
        GlobalCardRelationshipType,
        CardRelationship,
        CardContentBlock,
        CardAttachment,
        CardComment,
        Checklist,
        WhisperThread,
        WhisperMessage,
        WhisperParticipant,
    ):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        card.visibility = "SHARED"
        card.owner_user_id = None
        db.update(card)
    return user, project, card, SimpleNamespace(card=card_service)


def _args(discussion):
    user, project, card, service = discussion
    return service, user, project.get_uid(), card.get_uid(), HUMAN


def test_discussion_persists_replies_state_and_explicit_read_without_public_comments(discussion):
    args = _args(discussion)
    created = create_thread(*args, "Private human discussion")
    uid = created["thread"]["uid"]
    assert list_threads(*args)["threads"][0]["has_unread"] is False
    assert list_threads(*args)["threads"][0]["preview"] == "Private human discussion"
    with DbSession.use(readonly=False) as db:
        # A second actor is a real board member with the native read grant.
        other = User(
            firstname="Second",
            lastname="Reader",
            email="reader@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(other)
        db.insert(ProjectAssignedUser(project_id=discussion[1].id, user_id=other.id))
        db.insert(ProjectRole(project_id=discussion[1].id, user_id=other.id, actions=[ProjectRoleAction.Read]))
    second = (args[0], other, *args[2:])
    assert list_threads(*second)["threads"][0]["has_unread"] is True
    messages = read_messages(*second, uid)["messages"]
    assert messages[0]["content"] == "Private human discussion"
    # GET is non-mutating; only the explicit acknowledgement marks a page read.
    assert list_threads(*second)["threads"][0]["has_unread"] is True
    mark_read(*second, uid, messages[0]["uid"])
    assert (
        next(item for item in list_participants(*second, uid)["participants"] if item["user_uid"] == other.get_uid())[
            "has_unread"
        ]
        is False
    )
    assert list_threads(*second)["threads"][0]["has_unread"] is False
    reply(*args, uid, "Follow-up")
    assert (
        next(item for item in list_participants(*second, uid)["participants"] if item["user_uid"] == other.get_uid())[
            "has_unread"
        ]
        is True
    )
    assert list_threads(*second)["threads"][0]["has_unread"] is True
    assert set_state(*args, uid, "RESOLVED")["thread"]["state"] == "RESOLVED"
    with pytest.raises(ValueError):
        reply(*args, uid, "Blocked until reopened")
    set_state(*args, uid, "OPEN")
    assert len(read_messages(*args, uid)["messages"]) == 2
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.column(func.count()).select_from(CardComment)).first() == 0
    assert all(model.notification_data(None) == {} for model in (WhisperThread, WhisperMessage, WhisperParticipant))


def test_relationship_thread_is_one_object_on_both_cards_and_disappears_after_private_conversion(discussion):
    user, project, card, service = discussion
    with DbSession.use(readonly=False) as db:
        other_project = Project(owner_id=user.id, title="Other readable board")
        db.insert(other_project)
        other = Card(
            project_id=other_project.id, title="Other anchor", visibility="INTERNAL", created_by_user_id=user.id
        )
        db.insert(other)
        relation_type = GlobalCardRelationshipType(parent_name="Related", child_name="Related")
        db.insert(relation_type)
        relation = CardRelationship(
            card_id_parent=card.id, card_id_child=other.id, relationship_type_id=relation_type.id
        )
        db.insert(relation)
    first = _args(discussion)
    second = (service, user, other_project.get_uid(), other.get_uid(), HUMAN)
    uid = create_thread(*first, "Shared relation discussion", relationship_uid=relation.get_uid())["thread"]["uid"]
    assert list_threads(*first)["threads"][0]["uid"] == uid
    assert list_threads(*second)["threads"][0]["uid"] == uid
    reply(*second, uid, "Reply from the other card")
    assert len(read_messages(*first, uid)["messages"]) == 2
    with DbSession.use(readonly=False) as db:
        other.visibility = "PRIVATE"
        other.owner_user_id = user.id
        db.update(other)
    assert list_threads(*first)["threads"] == []
    with pytest.raises(WhisperUnavailable):
        read_messages(*first, uid)


@pytest.mark.parametrize("channel", [CollaborationChannel.Api, CollaborationChannel.Mcp, CollaborationChannel.Bot])
def test_nonhuman_channels_cannot_discover_or_mutate_discussions(discussion, channel):
    args = _args(discussion)
    uid = create_thread(*args, "Hidden")["thread"]["uid"]
    other = (*args[:4], channel)
    for operation in (
        lambda: list_threads(*other),
        lambda: create_thread(*other, "Denied"),
        lambda: read_messages(*other, uid),
        lambda: reply(*other, uid, "Denied"),
        lambda: set_state(*other, uid, "RESOLVED"),
    ):
        with pytest.raises(WhisperUnavailable):
            operation()


def test_current_policy_and_account_revocation_hide_existing_threads(discussion, monkeypatch):
    args = _args(discussion)
    uid = create_thread(*args, "Hidden after revocation")["thread"]["uid"]
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    with pytest.raises(WhisperUnavailable):
        read_messages(*args, uid)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    with DbSession.use(readonly=False) as db:
        user = discussion[0].model_copy(deep=True)
        user.activated_at = None
        db.update(user)
    with pytest.raises(WhisperUnavailable):
        list_threads(*args)


@pytest.mark.parametrize(
    "kind,model,values",
    [
        ("content_block", CardContentBlock, {"block_type": "rich_text", "payload": {}}),
        ("comment", CardComment, {}),
        ("checklist", Checklist, {"title": "Check"}),
        (
            "attachment",
            CardAttachment,
            {
                "filename": "fixture.pdf",
                "file": FileModel(
                    storage_type="test",
                    storage_name="test",
                    path="/tmp/fixture",
                    filename="fixture.pdf",
                    original_filename="fixture.pdf",
                ),
            },
        ),
    ],
)
def test_card_subanchors_validate_current_same_card_provenance(discussion, kind, model, values):
    values = dict(values)
    if model in (CardAttachment, CardComment):
        values["user_id"] = discussion[0].id
    args = _args(discussion)
    with DbSession.use(readonly=False) as db:
        valid = model(card_id=discussion[2].id, **values)
        db.insert(valid)
        foreign = Card(project_id=discussion[1].id, title="Other", visibility="SHARED")
        db.insert(foreign)
        invalid = model(card_id=foreign.id, **values)
        db.insert(invalid)
    created = create_thread(*args, "Anchored", subanchor_type=kind, subanchor_uid=valid.get_uid())
    assert created["thread"]["anchor"]["subanchor_uid"] == valid.get_uid()
    with pytest.raises(WhisperUnavailable):
        create_thread(*args, "Foreign anchor", subanchor_type=kind, subanchor_uid=invalid.get_uid())


def test_bounded_pagination_and_foreign_read_cursor(discussion):
    args = _args(discussion)
    first = create_thread(*args, "First")["thread"]["uid"]
    second = create_thread(*args, "Second")["thread"]["uid"]
    for n in range(4):
        reply(*args, second, f"Reply {n}")
    page = list_threads(*args, limit=1)
    assert page["threads"][0]["uid"] == second
    assert list_threads(*args, before_uid=page["next_cursor"], limit=1)["threads"][0]["uid"] == first
    messages = read_messages(*args, second, limit=2)
    assert len(messages["messages"]) == 2
    assert messages["next_cursor"]
    foreign_message = read_messages(*args, first)["messages"][0]["uid"]
    with pytest.raises(WhisperUnavailable):
        mark_read(*args, second, foreign_message)
    for size in (0, 51):
        with pytest.raises(ValueError):
            list_threads(*args, limit=size)


def test_migration_tables_match_native_models_and_preserve_retained_discussions(current_card):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import IntegrityError

    user, project, card, _ = current_card
    engine = DbEngine.get_main_engine()
    GlobalCardRelationshipType.__table__.create(engine)
    CardRelationship.__table__.create(engine)
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261018050000-c84d52a9e610.py"
    spec = importlib.util.spec_from_file_location("whisper_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        for model in (WhisperThread, WhisperMessage, WhisperParticipant):
            actual = {column["name"] for column in inspect(connection).get_columns(model.__tablename__)}
            assert actual == set(model.__table__.columns.keys())
            assert all(
                key["options"].get("ondelete") == "CASCADE"
                for key in inspect(connection).get_foreign_keys(model.__tablename__)
            )
    with DbSession.use(readonly=False) as db:
        thread = WhisperThread(project_id=project.id, card_id=card.id, creator_id=user.id)
        db.insert(thread)
        message = WhisperMessage(thread_id=thread.id, author_id=user.id, content="Retained")
        db.insert(message)
        db.insert(WhisperParticipant(thread_id=thread.id, user_id=user.id, last_read_message_id=message.id))
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="retained"):
            module.downgrade()
        assert connection.execute(text("SELECT content FROM whisper_message")).scalar_one() == "Retained"
        for mutation in ("state='INVALID'", "subanchor_uid='orphan'", "card_id=NULL"):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(f"UPDATE whisper_thread SET {mutation}"))
        for table in ("whisper_participant", "whisper_message", "whisper_thread"):
            connection.execute(text(f"DELETE FROM {table}"))
        module.downgrade()
        assert not inspect(connection).has_table("whisper_thread")


def test_http_wrapper_uses_server_channel_and_never_caches_hidden_discussions(discussion):
    import json
    from langboard.routes.board.WhisperThreadApi import _run

    args = _args(discussion)
    service, user, project_uid, card_uid, _ = args
    for scope in (
        {},
        {"collaboration_channel": CollaborationChannel.Api},
        {"collaboration_channel": CollaborationChannel.Mcp},
    ):
        response = _run(list_threads, SimpleNamespace(scope=scope), user, service, project_uid, card_uid)
        assert response.status_code == 404
        assert json.loads(response.body) == {"detail": "Not found"}
        assert response.headers["cache-control"] == "no-store"
    response = _run(
        create_thread,
        SimpleNamespace(scope={"collaboration_channel": HUMAN}),
        user,
        service,
        project_uid,
        card_uid,
        "Human only",
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_mentions_add_only_current_internal_humans_without_public_activity(discussion, monkeypatch):
    from langboard_shared.domain.services.WhisperThreads import add_participants, list_participants

    args = _args(discussion)
    with DbSession.use(readonly=False) as db:
        other = User(
            firstname="Mentioned",
            lastname="Human",
            email="mentioned@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(other)
        db.insert(ProjectAssignedUser(project_id=discussion[1].id, user_id=other.id))
        db.insert(ProjectRole(project_id=discussion[1].id, user_id=other.id, actions=[ProjectRoleAction.Read]))
        outsider = User(
            firstname="Outside",
            lastname="Human",
            email="outside@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(outsider)
    created = create_thread(*args, "Please review @Mentioned", mention_uids=[other.get_uid(), other.get_uid()])
    uid = created["thread"]["uid"]
    assert created["message"]["mention_uids"] == [other.get_uid()]
    participants = list_participants(*args, uid)["participants"]
    assert {item["user_uid"] for item in participants} == {args[1].get_uid(), other.get_uid()}
    assert next(item for item in participants if item["user_uid"] == other.get_uid())["last_read_message_uid"] is None
    add_participants(*args, uid, [other.get_uid()])
    assert len(list_participants(*args, uid)["participants"]) == 2
    with pytest.raises(WhisperUnavailable):
        reply(*args, uid, "Do not persist", mention_uids=[outsider.get_uid()])
    assert len(read_messages(*args, uid)["messages"]) == 1
    with pytest.raises(ValueError):
        create_thread(*args, "Too many", mention_uids=[other.get_uid()] * 11)
    # A revoked account cannot be added through a stale picker selection.
    with DbSession.use(readonly=False) as db:
        other.activated_at = None
        db.update(other)
    with pytest.raises(WhisperUnavailable):
        add_participants(*args, uid, [other.get_uid()])
    assert len(list_participants(*args, uid)["participants"]) == 1
    for channel in (CollaborationChannel.Api, CollaborationChannel.Mcp):
        forbidden = (*args[:4], channel)
        with pytest.raises(WhisperUnavailable):
            list_participants(*forbidden, uid)
        with pytest.raises(WhisperUnavailable):
            add_participants(*forbidden, uid, [args[1].get_uid()])


def test_thread_page_has_constant_query_count_for_one_or_twenty_five_threads(discussion):
    from sqlalchemy import event

    args = _args(discussion)
    engine = DbEngine.get_main_engine()
    queries = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    create_thread(*args, "First")
    event.listen(engine, "before_cursor_execute", record)
    try:
        list_threads(*args)
        first_count = len(queries)
        event.remove(engine, "before_cursor_execute", record)
        for n in range(24):
            create_thread(*args, f"Thread {n}")
        event.listen(engine, "before_cursor_execute", record)
        queries.clear()
        assert len(list_threads(*args)["threads"]) == 25
        assert len(queries) == first_count == 4
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_native_http_routes_enforce_transport_and_preserve_human_discussions(discussion, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board import WhisperThreadApi
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.security import Auth

    user, project, card, service = discussion
    service.close = lambda: None
    # Isolate credential verification, not native channel derivation or AuthFilter.
    monkeypatch.setattr(
        Auth, "validate", lambda headers: user if headers.get("authorization") == "Bearer fixture-session" else 401
    )
    monkeypatch.setattr(Auth, "validate_user_by_api_token", lambda _: user)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in AppRouter.api.routes:
        if getattr(getattr(route, "endpoint", None), "__module__", None) == WhisperThreadApi.__name__:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    base = f"/board/{project.get_uid()}/card/{card.get_uid()}/whispers"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        api_headers = {AuthSecurity.API_TOKEN_HEADER: "fixture-api", "X-Collaboration-Channel": "human_ui"}
        denied = client.get(base, headers=api_headers)
        assert denied.status_code == 404
        assert denied.json() == {"detail": "Not found"}
        assert denied.headers["cache-control"] == "no-store"
        human = {"Authorization": "Bearer fixture-session"}
        created = client.post(base, headers=human, json={"content": "Native route discussion"})
        assert created.status_code == 200
        uid = created.json()["thread"]["uid"]
        assert client.get(base, headers=human).json()["threads"][0]["uid"] == uid
        assert client.post(f"{base}/{uid}/messages", headers=human, json={"content": "Reply"}).status_code == 200
        assert len(client.get(f"{base}/{uid}/messages", headers=human).json()["messages"]) == 2
        assert client.get(f"{base}/{uid}/participants", headers=api_headers).status_code == 404
        assert (
            client.post(
                base, headers=human, json={"content": "Rejected", "collaboration_channel": "human_ui"}
            ).status_code
            == 400
        )
        assert len(client.get(base, headers=human).json()["threads"]) == 1
        assert client.patch(f"{base}/{uid}", headers=human, json={"state": "RESOLVED"}).status_code == 200
        assert client.post(f"{base}/{uid}/messages", headers=human, json={"content": "Cannot reply"}).status_code == 400


@pytest.mark.parametrize(
    "channel,visibility,mode,allowed",
    [
        (HUMAN, "SHARED", "project_members", True),
        (HUMAN, "PRIVATE", "project_members", False),
        (HUMAN, "SHARED", "scim", False),
        (CollaborationChannel.Api, "SHARED", "project_members", False),
        (CollaborationChannel.Mcp, "SHARED", "project_members", False),
        (CollaborationChannel.Bot, "SHARED", "project_members", False),
    ],
)
def test_card_details_projects_only_current_human_whisper_capability(
    discussion, monkeypatch, channel, visibility, mode, allowed
):
    import json
    from langboard.routes.board import BoardCardApi
    from starlette.requests import Request

    actor, project, card, native = discussion
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: mode))
    if visibility == "PRIVATE":
        with DbSession.use(readonly=False) as db:
            card.visibility, card.owner_user_id = visibility, actor.id
            db.update(card)
    empty = SimpleNamespace(get_api_list_by_card=lambda _: [])
    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_canonical_card=native.card.resolve_canonical_card,
            get_details=lambda *args, **kwargs: {},
            can_delete=lambda *args: False,
        ),
        project=SimpleNamespace(get_user_role_actions_by_project=lambda *args: ["read"]),
        app_setting=SimpleNamespace(get_api_global_relationship_list=lambda: []),
        project_column=SimpleNamespace(get_api_list_by_project=lambda *args, **kwargs: []),
        project_label=SimpleNamespace(get_api_list_by_project=lambda *args: []),
        checklist=empty,
        card_attachment=empty,
    )
    monkeypatch.setattr(BoardCardApi, "visible_linked_wikis", lambda *args: [])
    monkeypatch.setattr(BoardCardApi, "receipt_history", lambda *args: [])
    response = BoardCardApi.get_card_details(
        project.get_uid(),
        card.get_uid(),
        Request({"type": "http", "query_string": b"", "collaboration_channel": channel}),
        actor,
        service,
    )
    assert json.loads(response.body)["whispers_allowed"] is allowed


def test_native_relationship_edit_preserves_unchanged_discussion_identity(discussion, monkeypatch):
    from contextlib import contextmanager
    from importlib import import_module
    from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import (
        CardRelationshipRepository,
    )

    module = import_module("langboard_shared.domain.services.factory.CardRelationshipService")
    actor, project, card, native = discussion
    with DbSession.use(readonly=False) as db:
        other = Card(project_id=project.id, title="Related", visibility="SHARED")
        added = Card(project_id=project.id, title="Added", visibility="SHARED")
        db.insert(other)
        db.insert(added)
        relation_type = GlobalCardRelationshipType(parent_name="Reference", child_name="Reference")
        db.insert(relation_type)
        edge = CardRelationship(card_id_parent=card.id, card_id_child=other.id, relationship_type_id=relation_type.id)
        db.insert(edge)
    uid = create_thread(*_args(discussion), "Keep this relationship discussion", relationship_uid=edge.get_uid())[
        "thread"
    ]["uid"]

    @contextmanager
    def readiness():
        yield SimpleNamespace(watch=lambda *_: None)

    monkeypatch.setattr(module, "execution_readiness_uow", readiness)
    monkeypatch.setattr(module, "require_card_app_mutation", lambda *args: None)
    service = module.CardRelationshipService(
        lambda _: SimpleNamespace(publish_work_states=lambda *args: None),
        lambda _: None,
        SimpleNamespace(card_relationship=CardRelationshipRepository(None, None)),
    )
    monkeypatch.setattr(service, "_mark_card_changed_for_unread", lambda *args: None)
    monkeypatch.setattr(service, "dispatch_updated", lambda *args, **kwargs: None)
    relations = [(other.get_uid(), relation_type.get_uid()), (added.get_uid(), relation_type.get_uid())]
    service.update(actor, project, card, False, relations)
    assert read_messages(*_args(discussion), uid)["messages"][0]["content"] == "Keep this relationship discussion"
    with DbSession.use(readonly=False) as db:
        assert (
            db.exec(SqlBuilder.select.table(CardRelationship).where(CardRelationship.id == edge.id)).first() is not None
        )
    service.update(actor, project, card, False, relations[1:])
    assert list_threads(*_args(discussion))["threads"] == []
    if DbEngine.get_main_engine().dialect.name == "postgresql":
        with DbSession.use(readonly=False) as db:
            for model in (WhisperThread, WhisperMessage, WhisperParticipant):
                assert db.exec(SqlBuilder.select.column(func.count()).select_from(model)).first() == 0
