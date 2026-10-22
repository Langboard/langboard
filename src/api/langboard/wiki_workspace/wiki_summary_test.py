"""Bounded wiki summaries preserve native visibility and defer all bodies."""

import json
from types import SimpleNamespace
import pytest
from fastmcp.exceptions import AuthorizationError
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    User,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from .infrastructure import NativeWikiRepository


@pytest.fixture
def summaries(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, Bot, Project, ProjectAssignedUser, ProjectRole, ProjectWiki, ProjectWikiAssignedUser):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "wiki-summary-test-key-with-at-least-32-bytes"))
    monkeypatch.setattr(Cache, "get", lambda *args, **kwargs: None)
    monkeypatch.setattr(Cache, "set", lambda *args, **kwargs: None)
    user = User(
        id=1,
        firstname="Reader",
        lastname="Test",
        email="reader@example.invalid",
        password="test",
        activated_at=SafeDateTime.now(),
    )
    owner = User(
        id=2,
        firstname="Owner",
        lastname="Test",
        email="owner@example.invalid",
        password="test",
        activated_at=SafeDateTime.now(),
    )
    project = Project(id=10, owner_id=2, title="Summary test")
    other = Project(id=11, owner_id=2, title="Other project")
    member = ProjectAssignedUser(id=20, project_id=10, user_id=1)
    role = ProjectRole(id=30, project_id=10, user_id=1, actions=["read"])
    wikis = [
        ProjectWiki(
            id=100 + i,
            project_id=10,
            title=f"Wiki {i:02}",
            content=EditorContentModel(content=f"BODY {i} " + "x" * 32000),
        )
        for i in range(57)
    ]
    # Hidden records surround page boundaries and must not consume a page slot.
    for i in (0, 24, 25, 26, 50):
        wikis[i].is_public = False
    wikis[2].deleted_at = SafeDateTime.now()
    assigned = ProjectWikiAssignedUser(id=40, project_assigned_id=20, project_wiki_id=125, user_id=1)
    # An obsolete membership ID must not grant a private wiki after rejoining.
    obsolete = ProjectWikiAssignedUser(id=41, project_assigned_id=999, project_wiki_id=126, user_id=1)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all(
            [
                user,
                owner,
                project,
                other,
                member,
                role,
                *wikis,
                assigned,
                obsolete,
                ProjectWiki(id=200, project_id=11, title="Foreign secret"),
            ]
        )
        db.commit()
    service = DomainService()
    repository = NativeWikiRepository(user, service)
    yield SimpleNamespace(
        engine=engine,
        repository=repository,
        service=service,
        user=user,
        owner=owner,
        project=project,
        other=other,
        wikis=wikis,
        assigned=assigned,
    )
    service.close()
    engine.dispose()


def test_summary_pages_filter_before_limit_and_never_select_or_serialize_bodies(summaries, monkeypatch):
    queries = []
    event.listen(summaries.engine, "before_cursor_execute", lambda *args: queries.append((args[2], args[3])))
    monkeypatch.setattr(ProjectWiki, "api_response", lambda _: pytest.fail("Summary serialized a full wiki"))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("Summary used a stale replica"))
    first = summaries.repository.list_summaries(summaries.project.get_uid(), None, 25)
    second = summaries.repository.list_summaries(summaries.project.get_uid(), first["next_cursor"], 25)
    third = summaries.repository.list_summaries(summaries.project.get_uid(), second["next_cursor"], 25)
    assert [len(page["items"]) for page in (first, second, third)] == [25, 25, 2]
    expected = [wiki.get_uid() for i, wiki in enumerate(summaries.wikis) if i not in (0, 2, 24, 26, 50)]
    assert [item["wiki_uid"] for page in (first, second, third) for item in page["items"]] == expected
    assert third["next_cursor"] is None
    assert all(
        set(item) == {"wiki_uid", "title", "is_public"} for page in (first, second, third) for item in page["items"]
    )
    assert "BODY" not in json.dumps([first, second, third])
    wiki_queries = [(sql, params) for sql, params in queries if "FROM project_wiki " in sql]
    assert len(wiki_queries) == 3
    for sql, params in wiki_queries:
        assert "project_wiki.content" not in sql
        assert "LIMIT" in sql and params[-2:] == (26, 0)
        assert "project_assigned_user" in sql


def test_summary_cursor_is_scoped_and_invalid_cursors_fail_closed(summaries):
    repository = summaries.repository
    first = repository.list_summaries(summaries.project.get_uid(), None, 1)
    cursor = first["next_cursor"]
    for invalid in ("", "?", "x" * 2049, "e30=", summaries.wikis[1].get_uid(), cursor[:-4] + "AAAA"):
        with pytest.raises(ValueError, match="cursor"):
            repository.list_summaries(summaries.project.get_uid(), invalid, 1)
    owner_repository = NativeWikiRepository(summaries.owner, summaries.service)
    with pytest.raises(ValueError, match="cursor"):
        owner_repository.list_summaries(summaries.project.get_uid(), cursor, 1)
    owner_cursor = owner_repository.list_summaries(summaries.project.get_uid(), None, 1)["next_cursor"]
    with pytest.raises(ValueError, match="cursor"):
        owner_repository.list_summaries(summaries.other.get_uid(), owner_cursor, 1)


@pytest.mark.parametrize("limit", [0, 51, -1, True])
def test_summary_page_size_is_bounded(summaries, limit):
    with pytest.raises(ValueError, match="limit"):
        summaries.repository.list_summaries(summaries.project.get_uid(), None, limit)


def test_permissions_are_reapplied_between_pages(summaries):
    repository = summaries.repository
    first = repository.list_summaries(summaries.project.get_uid(), None, 1)
    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectWikiAssignedUser).where(ProjectWikiAssignedUser.id == summaries.assigned.id))
        db.exec(update(ProjectWiki).where(ProjectWiki.id == 103).values(is_public=False))
        db.exec(update(ProjectWiki).where(ProjectWiki.id == 104).values(deleted_at=SafeDateTime.now()))
    page = repository.list_summaries(summaries.project.get_uid(), first["next_cursor"], 50)
    assert {item["wiki_uid"] for item in page["items"]}.isdisjoint({summaries.wikis[i].get_uid() for i in (3, 4, 25)})


@pytest.mark.parametrize("mutation", ["membership", "role", "user_deleted", "user_inactive", "project_deleted"])
def test_current_authority_revocation_rejects_continuation(summaries, mutation):
    cursor = summaries.repository.list_summaries(summaries.project.get_uid(), None, 1)["next_cursor"]
    with DbSession.use(readonly=False) as db:
        if mutation == "membership":
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.id == 20))
        elif mutation == "role":
            db.exec(update(ProjectRole).where(ProjectRole.id == 30).values(actions=[]))
        elif mutation == "user_deleted":
            db.exec(update(User).where(User.id == 1).values(deleted_at=SafeDateTime.now()))
        elif mutation == "user_inactive":
            db.exec(update(User).where(User.id == 1).values(activated_at=None))
        else:
            db.exec(update(Project).where(Project.id == 10).values(deleted_at=SafeDateTime.now()))
    with pytest.raises(AuthorizationError):
        summaries.repository.list_summaries(summaries.project.get_uid(), cursor, 1)


def test_owner_and_current_member_admin_keep_native_private_visibility(summaries):
    owner = NativeWikiRepository(summaries.owner, summaries.service)
    assert owner.list_summaries(summaries.project.get_uid(), None, 1)["items"][0]["title"] == "Wiki 00"
    with DbSession.use(readonly=False) as db:
        db.exec(update(User).where(User.id == 1).values(is_admin=True))
    assert summaries.repository.list_summaries(summaries.project.get_uid(), None, 1)["items"][0]["title"] == "Wiki 00"
    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.id == 20))
    with pytest.raises(AuthorizationError):
        summaries.repository.list_summaries(summaries.project.get_uid(), None, 1)


def test_summary_http_bounds_authorization_and_legacy_full_detail_compatibility(summaries, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board.BoardWikiApi import (
        get_project_wiki_details,
        get_project_wiki_summaries,
        get_project_wikis,
    )

    service = summaries.service
    monkeypatch.setattr(service, "close", lambda: None)
    monkeypatch.setattr(service.project_wiki.repo.card, "get_linked_resource_map", lambda *_: {})
    monkeypatch.setattr(service.project_wiki, "get_linked_card_uid", lambda *_: None)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    endpoints = (get_project_wiki_summaries, get_project_wikis, get_project_wiki_details)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in AppRouter.api.routes:
        if getattr(route, "endpoint", None) in endpoints:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(summaries.user.id)
    prefix = f"/board/{summaries.project.get_uid()}"
    with TestClient(app) as client:
        assert client.get(f"{prefix}/wikis/summaries").status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        first = client.get(f"{prefix}/wikis/summaries", headers=headers)
        assert first.status_code == 200, first.text
        assert len(first.json()["items"]) == 25
        assert first.headers["cache-control"] == "no-store"
        next_page = client.get(
            f"{prefix}/wikis/summaries", headers=headers, params={"cursor": first.json()["next_cursor"]}
        )
        assert next_page.status_code == 200 and len(next_page.json()["items"]) == 25
        assert client.get(f"{prefix}/wikis/summaries", headers=headers, params={"cursor": "invalid"}).status_code == 400
        assert client.get(f"{prefix}/wikis/summaries", headers=headers, params={"limit": 51}).status_code == 400
        full = client.get(f"{prefix}/wikis", headers=headers)
        assert full.status_code == 200, full.text
        assert len(full.json()["wikis"]) == 56
        readable = next(wiki for wiki in full.json()["wikis"] if not wiki["forbidden"])
        assert readable["content"]["content"].startswith("BODY")
        detail = client.get(f"{prefix}/wiki/{readable['uid']}", headers=headers)
        assert detail.status_code == 200 and detail.json()["wiki"]["content"] == readable["content"]
        with DbSession.use(readonly=False) as db:
            db.exec(update(ProjectRole).where(ProjectRole.id == 30).values(actions=[]))
        assert client.get(f"{prefix}/wikis/summaries", headers=headers).status_code == 403
