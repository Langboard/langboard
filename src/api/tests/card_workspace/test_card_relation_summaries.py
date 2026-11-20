"""Native database proof for lazy, authorized, body-free relationship summaries."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardRelationship,
    GlobalCardRelationshipType,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.factory.CardRelationshipService import CardRelationshipService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import CardRelationshipRepository
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool


@pytest.fixture
def relations(monkeypatch):
    url = os.getenv("LANGBOARD_SUMMARY_TEST_DATABASE_URL", "sqlite://")
    schema = None
    if url.startswith("postgresql"):
        admin = create_engine(url)
        schema = "relations_summary_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={
            "options": f"-csearch_path={schema} -ctimezone=America/New_York -cstatement_timeout=10000"
        })
    else:
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        if schema:
            User.metadata.create_all(engine)
        else:
            for model in (User, Project, ProjectAssignedUser, ProjectRole, Card, GlobalCardRelationshipType, CardRelationship):
                model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        with DbSession.use(readonly=False) as db:
            actor = User(
                firstname="Tree",
                lastname="Actor",
                email="tree@example.invalid",
                password="fixture",
                activated_at=SafeDateTime.now(),
            )
            owner = User(
                firstname="Other",
                lastname="Owner",
                email="tree-other@example.invalid",
                password="fixture",
                activated_at=SafeDateTime.now(),
            )
            db.insert_all([actor, owner])
            home = Project(title="Home", owner_id=actor.id)
            remote = Project(title="Remote", owner_id=owner.id)
            denied = Project(title="Hidden board", owner_id=owner.id)
            db.insert_all([home, remote, denied])
            columns = {}
            if schema:
                for project in (home, remote, denied):
                    column = ProjectColumn(project_id=project.id, name="Fixture")
                    db.insert(column)
                    columns[project.id] = column.id
            db.insert(ProjectAssignedUser(project_id=remote.id, user_id=actor.id))
            role = ProjectRole(project_id=remote.id, user_id=actor.id, actions=["read"])
            db.insert(role)
            kind = GlobalCardRelationshipType(parent_name="Contains", child_name="Belongs to", machine_semantic="contains")
            db.insert(kind)
            cards = {}
            for name, project, visibility in [
                ("root", home, "SHARED"),
                ("private", home, "PRIVATE"),
                ("internal", home, "INTERNAL"),
                ("denied", denied, "SHARED"),
                ("child", home, "SHARED"),
                ("remote", remote, "SHARED"),
                ("archived", home, "SHARED"),
                ("deleted", home, "SHARED"),
                ("grandchild", home, "SHARED"),
            ]:
                card = Card(
                    project_id=project.id,
                    project_column_id=columns.get(project.id, 0),
                    title=name,
                    visibility=visibility,
                    owner_user_id=owner.id if visibility == "PRIVATE" else None,
                    created_by_user_id=owner.id if visibility == "PRIVATE" else actor.id,
                    description=EditorContentModel(content="BODY MUST NOT BE READ"),
                    ai_description="AI BODY MUST NOT BE READ",
                )
                if name == "archived":
                    card.archived_at = SafeDateTime.now()
                if name == "deleted":
                    card.deleted_at = SafeDateTime.now()
                db.insert(card)
                cards[name] = card
            edges = []
            for name in ("private", "internal", "denied", "child", "remote", "archived", "deleted"):
                edge = CardRelationship(
                    relationship_type_id=kind.id, card_id_parent=cards["root"].id, card_id_child=cards[name].id
                )
                db.insert(edge)
                edges.append(edge)
            db.insert(
                CardRelationship(
                    relationship_type_id=kind.id, card_id_parent=cards["child"].id, card_id_child=cards["grandchild"].id
                )
            )
            db.insert(
                CardRelationship(
                    relationship_type_id=kind.id, card_id_parent=cards["grandchild"].id, card_id_child=cards["root"].id
                )
            )
        repository = SimpleNamespace(card_relationship=CardRelationshipRepository(None, None))
        card_service = CardService(lambda _: None, lambda _: None, repository)
        monkeypatch.setattr(card_service, "_resolve_internal_access", lambda _: False)
        service = CardRelationshipService(lambda _: card_service, lambda _: None, repository)
        fixture = SimpleNamespace(
            service=service,
            actor=actor,
            owner=owner,
            home=home,
            remote=remote,
            denied=denied,
            cards=cards,
            edges=edges,
            kind=kind,
            role=role,
            engine=engine,
        )
        yield fixture
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def read(fixture, name="root", **kwargs):
    return fixture.service.get_api_relation_summaries(
        fixture.cards[name].project_id,
        fixture.cards[name].id,
        fixture.actor,
        channel=kwargs.pop("channel", CollaborationChannel.HumanUI),
        **kwargs,
    )


def test_summaries_page_only_visible_native_edges_without_bodies(relations):
    statements = []
    event.listen(relations.engine, "before_cursor_execute", lambda _, __, sql, *___: statements.append(sql))
    first = read(relations, limit=2)
    second = read(relations, limit=2, cursor=first["next_cursor"])
    assert [item["target"]["title"] for item in first["items"]] == ["child", "remote"]
    assert [item["target"]["title"] for item in second["items"]] == ["archived", "grandchild"]
    assert second["next_cursor"] is None
    assert first["limit"] == 2
    assert first["card"] == {
        "uid": relations.cards["root"].get_uid(),
        "project_uid": relations.home.get_uid(),
        "title": "root",
    }
    assert first["items"][1]["target"]["project_uid"] == relations.remote.get_uid()
    assert first["items"][0]["relationship"]["child_name"] == "Belongs to"
    assert second["items"][1]["relationship"]["child_card_uid"] == relations.cards["root"].get_uid()
    encoded = json.dumps([first, second], default=str)
    for name in ("private", "internal", "denied", "deleted"):
        assert relations.cards[name].get_uid() not in encoded
    assert "BODY MUST NOT BE READ" not in encoded
    assert all("card.description" not in sql and "card.ai_description" not in sql for sql in statements)
    edge_reads = [sql for sql in statements if "FROM card_relationship" in sql]
    assert len(edge_reads) == 2 and all("LIMIT" in sql for sql in edge_reads)


def test_expansion_is_one_hop_and_preserves_cycle_edges(relations):
    page = read(relations, "child")
    assert [item["target"]["title"] for item in page["items"]] == ["root", "grandchild"]
    assert all("items" not in item["target"] and "description" not in item["target"] for item in page["items"])


def test_membership_and_role_revocation_are_rechecked(relations):
    with DbSession.use(readonly=False) as db:
        relations.role.actions = ["update"]
        db.update(relations.role)
    assert "remote" not in [item["target"]["title"] for item in read(relations)["items"]]
    assert read(relations, "remote") is None
    with DbSession.use(readonly=False) as db:
        relations.actor.deleted_at = SafeDateTime.now()
        db.update(relations.actor)
    assert read(relations) is None


@pytest.mark.parametrize("actions", [None, [], ["update"]])
@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Api, CollaborationChannel.Mcp])
def test_member_admin_matches_native_grant_without_explicit_read_role(relations, actions, channel):
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
    from langboard_shared.domain.services.CardCrossBoardOperations import _grant

    stale_actor = relations.actor.model_copy(deep=True)
    with DbSession.use(readonly=False) as db:
        relations.actor.is_admin = True
        db.update(relations.actor)
        if actions is None:
            db.delete(relations.role)
        else:
            relations.role.actions = actions
            db.update(relations.role)
    with DbSession.use(readonly=False) as db:
        assert _grant(db, stale_actor, relations.remote, ProjectRoleAction.Read)
    relations.actor = stale_actor
    card_service = relations.service._get_service(CardService)
    assert card_service.resolve_readable_card(
        relations.remote, relations.cards["remote"], stale_actor, channel, metadata_only=True
    ) is not None
    contexts = card_service.resolve_work_visibility_contexts(stale_actor, channel)
    assert set(contexts) == {relations.home.id, relations.remote.id}
    assert read(relations, "remote", channel=channel) is not None
    page = read(relations, channel=channel)
    titles = [item["target"]["title"] for item in page["items"]]
    assert "remote" in titles
    assert not {"private", "internal", "denied", "deleted"}.intersection(titles)
    for name in ("private", "internal", "denied", "deleted"):
        assert read(relations, name, channel=channel) is None


@pytest.mark.parametrize("revocation", ["admin", "membership", "activation", "account", "project"])
def test_admin_summary_access_rechecks_current_authority(relations, revocation):
    with DbSession.use(readonly=False) as db:
        relations.actor.is_admin = True
        relations.role.actions = []
        db.update(relations.actor)
        db.update(relations.role)
    assert read(relations, "remote") is not None
    stale_admin = relations.actor.model_copy(deep=True)
    with DbSession.use(readonly=False) as db:
        if revocation == "membership":
            db.exec(SqlBuilder.delete.table(ProjectAssignedUser).where(
                ProjectAssignedUser.project_id == relations.remote.id,
                ProjectAssignedUser.user_id == relations.actor.id,
            ))
            relations.role.actions = ["*"]
            db.update(relations.role)
        elif revocation == "project":
            relations.remote.deleted_at = SafeDateTime.now()
            db.update(relations.remote)
        else:
            if revocation == "admin":
                relations.actor.is_admin = False
            elif revocation == "activation":
                relations.actor.activated_at = None
            else:
                relations.actor.deleted_at = SafeDateTime.now()
            db.update(relations.actor)
    relations.actor = stale_admin
    contexts = relations.service._get_service(CardService).resolve_work_visibility_contexts(
        stale_admin, CollaborationChannel.HumanUI
    )
    assert relations.remote.id not in contexts
    assert relations.denied.id not in contexts
    assert read(relations, "remote") is None
    if revocation in ("activation", "account"):
        assert contexts == {} and read(relations) is None
    else:
        assert relations.home.id in contexts
        assert "remote" not in [item["target"]["title"] for item in read(relations)["items"]]


def test_wrong_project_deleted_or_hidden_source_fail_closed(relations):
    assert (
        relations.service.get_api_relation_summaries(
            relations.remote.id,
            relations.cards["root"].id,
            relations.actor,
            channel=CollaborationChannel.HumanUI,
        )
        is None
    )
    for name in ("private", "internal", "denied", "deleted"):
        assert read(relations, name) is None


def test_private_source_requires_native_owner_channel_and_same_owner_target(relations):
    with DbSession.use(readonly=False) as db:
        root = relations.cards["root"]
        target = relations.cards["private"]
        root.visibility = target.visibility = "PRIVATE"
        root.owner_user_id = target.owner_user_id = relations.actor.id
        root.created_by_user_id = target.created_by_user_id = relations.actor.id
        db.update(root)
        db.update(target)
    assert [item["target"]["title"] for item in read(relations)["items"]] == ["private"]
    assert read(relations, channel=CollaborationChannel.Api) is None
    assert read(relations, channel=CollaborationChannel.Mcp)["items"][0]["target"]["title"] == "private"


@pytest.mark.parametrize("limit", [0, -1, 51, True])
def test_invalid_limits_are_rejected(relations, limit):
    with pytest.raises(ValueError):
        read(relations, limit=limit)


@pytest.mark.parametrize("cursor", ["garbage", "", "x" * 200, "1.invalid.invalid"])
def test_invalid_cursors_are_rejected(relations, cursor):
    with pytest.raises(ValueError):
        read(relations, cursor=cursor)


def test_cursor_belongs_to_its_node_and_pages_reauthorize(relations):
    page = read(relations, limit=1)
    with pytest.raises(ValueError):
        read(relations, "child", cursor=page["next_cursor"])
    with DbSession.use(readonly=False) as db:
        db.exec(
            SqlBuilder.delete.table(ProjectAssignedUser).where(ProjectAssignedUser.project_id == relations.remote.id)
        )
    next_page = read(relations, cursor=page["next_cursor"])
    assert [item["target"]["title"] for item in next_page["items"]] == ["archived", "grandchild"]


def test_native_http_summary_route_auth_limits_and_revocation(relations, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board import BoardApi  # noqa: F401 - register native routes
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    User.metadata.create_all(relations.engine)
    monkeypatch.setattr(CardService, "_resolve_internal_access", lambda *_: False)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(relations.actor.id)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    path = f"/board/{relations.home.get_uid()}/card/{relations.cards['root'].get_uid()}/relation-summaries"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers=headers, params={"limit": 2})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert [item["target"]["title"] for item in response.json()["items"]] == ["child", "remote"]
        assert client.get(path, headers=headers, params={"limit": 51}).status_code == 400
        assert client.get(path, headers=headers, params={"cursor": "x" * 25}).status_code == 400
        private_path = path.replace(relations.cards["root"].get_uid(), relations.cards["private"].get_uid())
        assert client.get(private_path, headers=headers).status_code == 404
        with DbSession.use(readonly=False) as db:
            relations.home.deleted_at = SafeDateTime.now()
            db.update(relations.home)
        assert client.get(path, headers=headers).status_code in (403, 404)
