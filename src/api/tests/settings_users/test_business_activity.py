"""Administrator metadata feed exercises native HTTP authorization and database scopes."""

import os
from datetime import timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.settings import UserSettingsApi  # noqa: F401
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    ProjectWiki,
    ProjectWikiActivity,
    SettingRole,
    User,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType
from langboard_shared.Env import Env
from sqlalchemy import create_engine, delete, event, update


@pytest.fixture(params=["sqlite", "postgresql"])
def business_activity_engine(request, tmp_path):
    database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if request.param == "postgresql" and not database_url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
    engine = create_engine(
        database_url if request.param == "postgresql" else f"sqlite:///{tmp_path / 'activity.sqlite'}"
    )
    models = (
        User,
        SettingRole,
        Project,
        ProjectAssignedUser,
        ProjectRole,
        ProjectColumn,
        Card,
        ProjectWiki,
        ProjectActivity,
        ProjectWikiActivity,
    )
    tables = {model.__table__ for model in models}
    if engine.dialect.name == "postgresql":
        pending = list(tables)
        while pending:
            for foreign_key in pending.pop().foreign_keys:
                referenced = foreign_key.column.table
                if referenced not in tables:
                    tables.add(referenced)
                    pending.append(referenced)
    metadata = User.__table__.metadata
    metadata.create_all(engine, tables=list(tables))
    try:
        yield engine
    finally:
        metadata.drop_all(engine, tables=list(tables))
        engine.dispose()


def test_business_activity_http_scope_pagination_and_filters(tmp_path, monkeypatch, business_activity_engine):
    engine = business_activity_engine
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    for name, value in {
        "CACHE_DIR": tmp_path / "cache",
        "FULL_ADMIN_ACCESS_EMAILS": [],
        "JWT_SECRET_KEY": "business-activity-native-test-secret-32",
        "CARD_INTERNAL_ACCESS_MODE": "project_members",
    }.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, v=value: v))
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        admin = User(
            email="admin@example.invalid",
            password="test",
            firstname="Admin",
            lastname="Test",
            is_admin=True,
            activated_at=now,
        )
        target = User(
            email="target@example.invalid", password="test", firstname="Target", lastname="Test", activated_at=now
        )
        denied = User(
            email="denied@example.invalid",
            password="test",
            firstname="Denied",
            lastname="Test",
            is_admin=True,
            activated_at=now,
        )
        regular = User(
            email="regular@example.invalid", password="test", firstname="Regular", lastname="Test", activated_at=now
        )
        db.insert_all([admin, target, denied, regular])
        db.insert_all(
            [
                SettingRole(user_id=admin.id, actions=["user_read"]),
                SettingRole(user_id=regular.id, actions=["user_read"]),
            ]
        )
        project = Project(owner_id=target.id, title="Visible board")
        hidden = Project(owner_id=target.id, title="Hidden board")
        db.insert_all([project, hidden])
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=admin.id))
        column = ProjectColumn(project_id=project.id, name="Activity column")
        db.insert(column)
        card = Card(project_id=project.id, project_column_id=column.id, title="Readable card", visibility="SHARED")
        private = Card(
            project_id=project.id,
            project_column_id=column.id,
            title="Secret private title",
            visibility="PRIVATE",
            owner_user_id=target.id,
            created_by_user_id=target.id,
        )
        wiki = ProjectWiki(project_id=project.id, title="Readable wiki", is_public=False)
        db.insert_all([card, private, wiki])
        history = {"body": "SECRET-BODY", "token": "SECRET-TOKEN"}
        first = ProjectActivity(
            project_id=project.id,
            card_id=card.id,
            user_id=target.id,
            activity_type=ProjectActivityType.CardCreated,
            activity_history=history,
            created_at=now - timedelta(minutes=3),
        )
        second = ProjectWikiActivity(
            project_id=project.id,
            project_wiki_id=wiki.id,
            user_id=target.id,
            activity_type=ProjectWikiActivityType.WikiUpdated,
            activity_history=history,
            created_at=now - timedelta(minutes=2),
        )
        db.insert_all(
            [
                first,
                second,
                ProjectActivity(
                    project_id=project.id,
                    user_id=target.id,
                    activity_type=ProjectActivityType.ProjectAssignedUsersUpdated,
                    activity_history=history,
                    created_at=now,
                ),
                ProjectWikiActivity(
                    project_id=project.id,
                    project_wiki_id=wiki.id,
                    user_id=target.id,
                    activity_type=ProjectWikiActivityType.WikiPublicityChanged,
                    activity_history=history,
                    created_at=now,
                ),
                ProjectActivity(
                    project_id=project.id,
                    card_id=private.id,
                    user_id=target.id,
                    activity_type=ProjectActivityType.CardCreated,
                    activity_history=history,
                    created_at=now,
                ),
                ProjectActivity(
                    project_id=hidden.id,
                    user_id=target.id,
                    activity_type=ProjectActivityType.ProjectUpdated,
                    activity_history=history,
                    created_at=now,
                ),
            ]
        )
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    url = f"/settings/users/{target.get_uid()}/business-activity"
    try:
        with TestClient(app) as client:

            def login(user):
                access, refresh = AuthSecurity.authenticate(user.id)
                client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                return {"Authorization": f"Bearer {access}"}

            assert client.get(url).status_code == 401
            assert client.get(url, headers=login(denied)).status_code == 403
            assert client.get(url, headers=login(regular)).status_code == 403
            headers = login(admin)
            response = client.get(url, headers=headers, params={"limit": 1})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            page = response.json()
            assert len(page["records"]) == 1 and page["has_more"]
            assert page["records"][0]["action"] == "wiki_updated"
            assert page["records"][0]["actor_principal_uid"] == target.get_uid()
            response2 = client.get(url, headers=headers, params={"limit": 1, "page": 2})
            assert response2.status_code == 200, response2.text
            assert response2.json()["records"][0]["action"] == "card_created"
            assert not response2.json()["has_more"], "Hidden records must not affect the sentinel"
            assert client.get(url, headers=headers, params={"page": 3, "limit": 1}).json()["records"] == []
            statements = []

            def capture(_conn, _cursor, statement, _parameters, _context, _many):
                statements.append(statement)

            event.listen(engine, "before_cursor_execute", capture)
            all_events = client.get(url, headers=headers)
            event.remove(engine, "before_cursor_execute", capture)
            assert all("activity_history" not in statement for statement in statements)

            for secret in ("SECRET-BODY", "SECRET-TOKEN", "Secret private title", "Hidden board", "activity_history"):
                assert secret not in all_events.text
            assert len(all_events.json()["records"]) == 2
            for security_action in ("project_assigned_users_updated", "wiki_publicity_changed"):
                excluded = client.get(url, headers=headers, params={"action": security_action, "limit": 1})
                assert excluded.status_code == 200, excluded.text
                assert excluded.json()["records"] == [] and not excluded.json()["has_more"]
            filtered = client.get(url, headers=headers, params={"action": "card_created", "limit": 1})
            assert len(filtered.json()["records"]) == 1 and not filtered.json()["has_more"]
            assert client.get(url, headers=headers, params={"project_uid": hidden.get_uid()}).json()["records"] == []
            assert client.get(url, headers=headers, params={"occurred_from": now.isoformat()}).json()["records"] == []
            assert (
                len(
                    client.get(
                        url,
                        headers=headers,
                        params={
                            "occurred_from": (now - timedelta(minutes=3)).isoformat(),
                            "occurred_before": (now - timedelta(minutes=2)).isoformat(),
                        },
                    ).json()["records"]
                )
                == 1
            )
            for params, code in (
                ({"limit": 101}, 400),
                ({"page": 0}, 400),
                ({"occurred_from": "2026-01-01T00:00:00"}, 400),
                ({"occurred_from": now.isoformat(), "occurred_before": now.isoformat()}, 400),
            ):
                assert client.get(url, headers=headers, params=params).status_code == code
            # Deleted resource references are excluded before pages and sentinels.
            with engine.begin() as db:
                db.execute(update(Card).where(Card.id == card.id).values(deleted_at=now))
                db.execute(update(ProjectWiki).where(ProjectWiki.id == wiki.id).values(deleted_at=now))
            deleted_resources = client.get(url, headers=headers)
            assert deleted_resources.status_code == 200, deleted_resources.text
            assert deleted_resources.json()["records"] == [] and not deleted_resources.json()["has_more"]
            with engine.begin() as db:
                db.execute(update(Card).where(Card.id == card.id).values(deleted_at=None))
                db.execute(update(ProjectWiki).where(ProjectWiki.id == wiki.id).values(deleted_at=None))
            assert len(client.get(url, headers=headers).json()["records"]) == 2
            assert client.get("/settings/users/00000000000/business-activity", headers=headers).status_code == 404
            # Revocation takes effect on the next request, including the sentinel.
            with engine.begin() as db:
                db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == admin.id))
            revoked = client.get(url, headers=headers)
            assert revoked.status_code == 200, revoked.text
            assert revoked.json()["records"] == [] and not revoked.json()["has_more"]
    finally:
        engine.dispose()
