"""Settings list uses successful login history, never failed attempts or activity."""

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.routes.settings.UserSettingsApi import get_users_page_in_settings
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ProjectActivity, ProjectWikiActivity, User, UserProfile, UserSignInHistory
from langboard_shared.domain.services.factory.UserService import UserService
from langboard_shared.infrastructure.repositories.factory.UserRepository import UserRepository
from sqlalchemy import create_engine, event


def _create_settings_tables(engine, models):
    tables = {model.__table__ for model in models}
    if engine.dialect.name == "postgresql":
        # Native FK enforcement requires the real referenced schema, unlike SQLite defaults.
        pending = list(tables)
        while pending:
            for foreign_key in pending.pop().foreign_keys:
                referenced = foreign_key.column.table
                if referenced not in tables:
                    tables.add(referenced)
                    pending.append(referenced)
    metadata = models[0].__table__.metadata
    metadata.create_all(engine, tables=list(tables))
    return tables


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_identity_source_filters_live_links_before_count_and_pagination(tmp_path, monkeypatch, backend):
    from langboard_shared.domain.models import UserIdentityLink
    from langboard_shared.domain.models.UserIdentityLink import IdentityProvider
    from sqlalchemy import delete

    database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if backend == "postgresql" and not database_url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
    engine = create_engine(database_url if backend == "postgresql" else f"sqlite:///{tmp_path / 'identity.sqlite'}")
    tables = _create_settings_tables(
        engine, (User, UserProfile, UserSignInHistory, UserIdentityLink, ProjectActivity, ProjectWikiActivity)
    )
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    now = SafeDateTime.now()
    users = []
    try:
        with DbSession.atomic() as db:
            for index in range(5):
                user = User(
                    firstname="Identity",
                    lastname=str(index),
                    username=f"identity-{index}",
                    email=f"identity-{index}@example.invalid",
                    password="fixture-only",
                    activated_at=now if index != 1 else None,
                )
                db.insert(user)
                users.append(user)
            for index, provider in (
                (0, IdentityProvider.Oidc),
                (1, IdentityProvider.Scim),
                (2, IdentityProvider.Oidc),
                (2, IdentityProvider.Scim),
                (3, IdentityProvider.Oidc),
            ):
                db.insert(
                    UserIdentityLink(
                        user_id=users[index].id,
                        provider=provider,
                        external_id=f"identity-{index}",
                        issuer="https://fixture.invalid",
                    )
                )
        with DbSession.atomic() as db:
            db.exec(delete(UserIdentityLink).where(UserIdentityLink.column("user_id") == users[3].id))
        repository = UserRepository(lambda _: None, lambda _: None)
        roles = SimpleNamespace(
            **{name: SimpleNamespace(get_list=Mock(return_value=[])) for name in ("api_key", "setting", "mcp")}
        )
        service = UserService(lambda _: None, lambda _: None, SimpleNamespace(user=repository, role=roles))
        for provider, expected in (("all", range(5)), ("oidc", (0, 2)), ("scim", (1, 2))):
            expected_uids = [users[index].get_uid() for index in expected]
            args = dict(identity_provider=provider, sort_by="email", sort_direction="asc")
            assert service.count_in_settings(identity_provider=provider) == len(expected_uids)
            rows = service.get_api_list_in_settings(**args)
            assert [row["uid"] for row in rows] == expected_uids
            pages = [service.get_api_list_in_settings(offset=i, limit=1, **args) for i in range(len(rows) + 1)]
            assert [page[0]["uid"] for page in pages[:-1]] == expected_uids
            assert pages[-1] == []
        assert service.count_in_settings(identity_provider="scim", account_status="active") == 1
        assert service.count_in_settings(identity_provider="scim", query="IDENTITY-1") == 1
        assert service.count_in_settings(identity_provider="oidc", column_filters={"email": "identity-3"}) == 0
        assert service.count_in_settings(identity_provider="oidc", exclude_user_uid=users[0].get_uid()) == 1
        response = get_users_page_in_settings(
            page=2,
            limit=1,
            identity_provider="oidc",
            sort_by="email",
            sort_direction="asc",
            service=SimpleNamespace(user=service),
        )
        payload = json.loads(response.body)
        assert payload["total_records"] == 2
        assert [row["uid"] for row in payload["records"]] == [users[2].get_uid()]
        with pytest.raises(ValueError):
            service.count_in_settings(identity_provider="local")
    finally:
        User.__table__.metadata.drop_all(engine, tables=list(tables))
        engine.dispose()


def test_settings_login_aggregate_preserves_accounts_without_history(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sign-ins.sqlite'}")
    for model in (User, UserProfile, UserSignInHistory, ProjectActivity, ProjectWikiActivity):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    now = SafeDateTime.now()
    earlier = now.replace(year=now.year - 1)
    users = []
    with DbSession.atomic() as db:
        for index in range(4):
            user = User(
                firstname="Fixture",
                lastname=str(index),
                username=f"login-{index}",
                email=f"login-{index}@example.invalid",
                password="fixture-only",
                activated_at=now if index != 1 else None,
                deleted_at=now if index == 3 else None,
            )
            db.insert(user)
            users.append(user)
        db.insert(UserProfile(user_id=users[0].id, industry="test", purpose="test"))
        for user, succeeded, at in (
            (users[0], True, now),
            (users[0], True, earlier),
            (users[0], False, now.replace(year=now.year + 1)),
            (users[1], False, now),
            (users[3], True, now),
        ):
            db.insert(UserSignInHistory(user_id=user.id, is_success=succeeded, signed_in_at=at))
    statements = []
    event.listen(engine, "before_cursor_execute", lambda *_args: statements.append(_args[2]))
    repository = UserRepository(lambda _: None, lambda _: None)
    roles = SimpleNamespace(
        **{name: SimpleNamespace(get_list=Mock(return_value=[])) for name in ("api_key", "setting", "mcp")}
    )
    service = UserService(lambda _: None, lambda _: None, SimpleNamespace(user=repository, role=roles))
    try:
        result = service.get_api_list_in_settings()
        by_email = {row["email"]: row for row in result}
        assert len(result) == 3
        assert all(row["latest_business_activity_at"] is None for row in result)
        assert by_email[users[0].email]["last_signed_in_at"] == now
        assert by_email[users[0].email]["account_status"] == "active"
        assert by_email[users[1].email]["last_signed_in_at"] is None
        assert by_email[users[1].email]["account_status"] == "inactive"
        assert by_email[users[2].email]["last_signed_in_at"] is None
        assert by_email[users[2].email]["account_status"] == "active"
        assert len(statements) == 1, "The settings list must not query login history per user"
        assert service.count_in_settings() == 3
        assert service.count_in_settings(account_status="active") == 2
        assert service.count_in_settings(account_status="inactive") == 1
        pages = [service.get_api_list_in_settings(offset=index, limit=1) for index in range(4)]
        assert [page[0]["uid"] for page in pages[:3]] == [row["uid"] for row in result]
        assert pages[3] == []
        assert service.get_api_list_in_settings(query=users[0].get_uid())[0]["email"] == users[0].email
        assert (
            service.get_api_list_in_settings(query=" FIXTURE ", account_status="inactive")[0]["email"] == users[1].email
        )
        assert service.get_api_list_in_settings(query="LOGIN-0@EXAMPLE.INVALID")[0]["email"] == users[0].email
        assert service.get_api_list_in_settings(query="%") == []
        assert service.count_in_settings(query="_") == 0
        assert service.get_api_list_in_settings(query=users[3].get_uid()) == []
        # Detail lookup is an exact ID filter, never a fuzzy text search.
        with engine.begin() as db:
            from sqlalchemy import update

            db.execute(update(User).where(User.id == users[1].id).values(username=users[0].get_uid()))
        assert service.get_api_list_in_settings(user_uid=users[0].get_uid(), limit=1)[0]["uid"] == users[0].get_uid()
        assert service.get_api_list_in_settings(user_uid=users[3].get_uid(), limit=1) == []
        ascending = service.get_api_list_in_settings(sort_by="email", sort_direction="asc", limit=2)
        assert [row["email"] for row in ascending] == [users[0].email, users[1].email]
        assert (
            service.get_api_list_in_settings(column_filters={"email": "LOGIN-1", "firstname": "fixture"})[0]["uid"]
            == users[1].get_uid()
        )
        assert service.count_in_settings(column_filters={"email": "LOGIN-1"}) == 1
        assert service.count_in_settings(exclude_user_uid=users[0].get_uid()) == 2
        assert all(
            row["uid"] != users[0].get_uid()
            for row in service.get_api_list_in_settings(exclude_user_uid=users[0].get_uid())
        )
        statements.clear()
        assert service.get_api_list_in_settings(limit=1)[0]["uid"] == result[0]["uid"]
        assert len(statements) == 1, "Pagination must not add per-user queries"
        response = get_users_page_in_settings(
            page=2, limit=1, query="Fixture", account_status="all", service=SimpleNamespace(user=service)
        )
        payload = json.loads(response.body)
        assert payload["page"] == 2 and payload["limit"] == 1
        assert payload["total_records"] == 3
        assert [row["uid"] for row in payload["records"]] == [result[1]["uid"]]
        assert payload["records"][0]["account_status"] == result[1]["account_status"]

    finally:
        engine.dispose()


def test_settings_page_http_enforces_current_principal_role_and_query_bounds(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.models import ApiKeyRole, Card, CardComment, McpRole, Organization, SettingRole
    from langboard_shared.domain.services import DomainService
    from langboard_shared.Env import Env
    from langboard_shared.publishers import AppSettingPublisher, UserPublisher
    from sqlalchemy import select, update

    engine = create_engine(f"sqlite:///{tmp_path / 'settings-http.sqlite'}")
    for model in (
        User,
        UserProfile,
        UserSignInHistory,
        ProjectActivity,
        ProjectWikiActivity,
        SettingRole,
        ApiKeyRole,
        McpRole,
        Card,
        CardComment,
        Organization,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "http-fixture-signing-key-32-bytes-minimum"))
    for publisher, names in (
        (UserPublisher, ("updated", "deactivated", "deleted")),
        (AppSettingPublisher, ("user_updated", "selected_users_deleted")),
    ):
        for name in names:
            monkeypatch.setattr(publisher, name, Mock())
    with DbSession.atomic() as db:
        actor = User(
            firstname="HTTP",
            lastname="Admin",
            username="http-admin",
            email="http@example.invalid",
            password="fixture-only",
            activated_at=SafeDateTime.now(),
            is_admin=True,
        )
        db.insert(actor)
        role = SettingRole(user_id=actor.id, actions=["user_read"])
        db.insert(role)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = "/settings/users/page"
    detail_endpoint = f"/settings/users/{actor.get_uid()}"
    try:
        with TestClient(app) as client:
            assert client.get(endpoint).status_code == 401
            assert client.get(detail_endpoint).status_code == 401
            assert client.get(endpoint, headers=headers).status_code == 401
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            response = client.get(endpoint, headers=headers, params={"limit": 1, "query": "HTTP"})
            assert response.status_code == 200, response.text
            assert response.json()["total_records"] == 1
            assert response.json()["records"][0]["uid"] == actor.get_uid()
            detail = client.get(detail_endpoint, headers=headers)
            assert detail.status_code == 200, detail.text
            assert detail.json()["user"]["uid"] == actor.get_uid()
            assert detail.json()["user"]["setting_role_actions"] == ["user_read"]
            assert detail.json()["user"]["last_signed_in_at"] is None
            assert detail.json()["user"]["latest_business_activity_at"] is None
            assert not {"password", "session", "access_token", "refresh_token"} & detail.json()["user"].keys()
            assert client.get("/settings/users/invalid", headers=headers).status_code == 400
            for params in (
                {"last_signed_in_from": "2026-10-24T10:00:00"},
                {"last_signed_in_before": "2026-02-30T10:00:00Z"},
                {"last_signed_in_from": "1792807200"},
                {"last_signed_in_from": "2026-10-24T10:00:00+24:00"},
                {"last_signed_in_from": "2026-10-24T10:00:00+09:99"},
                {"last_signed_in_from": "2026-10-24T10:00:00Z", "last_signed_in_before": "2026-10-24T10:00:00Z"},
                {"last_signed_in_from": "2026-10-25T10:00:00Z", "last_signed_in_before": "2026-10-24T10:00:00Z"},
                {"last_business_activity_from": "2026-10-24T10:00:00"},
                {"last_business_activity_before": "2026-02-30T10:00:00Z"},
                {"last_business_activity_from": "1792807200"},
                {"last_business_activity_from": "2026-10-24T10:00:00+24:00"},
                {"last_business_activity_from": "2026-10-24T10:00:00+09:99"},
                {"last_business_activity_from": "x" * 41},
                {
                    "last_business_activity_from": "2026-10-24T10:00:00Z",
                    "last_business_activity_before": "2026-10-24T10:00:00Z",
                },
                {
                    "last_business_activity_from": "2026-10-25T10:00:00Z",
                    "last_business_activity_before": "2026-10-24T10:00:00Z",
                },
                {"page": 0},
                {"page": 1000001},
                {"limit": 0},
                {"limit": 101},
                {"query": "x" * 201},
                {"email": "x" * 201},
                {"firstname": "x" * 201},
                {"lastname": "x" * 201},
                {"account_status": "deleted"},
                {"identity_provider": "local"},
                {"sort_by": "password"},
                {"sort_direction": "sideways"},
                {"exclude_user_uid": "../"},
            ):
                invalid = client.get(endpoint, headers=headers, params=params)
                assert invalid.status_code == 400, (params, invalid.text)
                assert invalid.json()["code"] == "VA0000"
            with engine.begin() as db:
                db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["user_update"]))
            assert client.get(endpoint, headers=headers).status_code == 403
            assert client.get(detail_endpoint, headers=headers).status_code == 403
            with engine.begin() as db:
                db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["user_read"]))
                db.execute(update(User).where(User.id == actor.id).values(is_admin=False))
            assert client.get(endpoint, headers=headers).status_code == 403
            assert client.get(detail_endpoint, headers=headers).status_code == 403
            with engine.begin() as db:
                db.execute(update(User).where(User.id == actor.id).values(is_admin=True, activated_at=None))
            assert client.get(endpoint, headers=headers).status_code == 401
            assert client.get(detail_endpoint, headers=headers).status_code == 401

            # Restore the fixture actor, then exercise native mutation routes and repositories.
            with engine.begin() as db:
                db.execute(update(User).where(User.id == actor.id).values(activated_at=SafeDateTime.now()))
            with DbSession.atomic() as db:
                target = User(
                    firstname="Task",
                    lastname="Author",
                    username="task-author",
                    email="target@example.invalid",
                    password="fixture-only",
                    activated_at=SafeDateTime.now(),
                )
                db.insert(target)
                card = Card(title="Preserved work", project_id=1, project_column_id=1, created_by_user_id=target.id)
                db.insert(card)
                comment = CardComment(card_id=card.id, user_id=target.id)
                db.insert(comment)
                history = UserSignInHistory(
                    user_id=target.id, is_success=True, signed_in_at=datetime(2026, 10, 24, 1, tzinfo=timezone.utc)
                )
                db.insert(history)
                from langboard_shared.domain.models.ProjectActivity import ProjectActivityType

                db.insert(
                    ProjectActivity(
                        user_id=target.id,
                        project_id=1,
                        activity_type=ProjectActivityType.CardUpdated,
                        created_at=datetime(2026, 10, 24, 2, tzinfo=timezone.utc),
                    )
                )
            # Native HTTP: the matching user sits outside the unfiltered first page.
            unfiltered = client.get(
                endpoint, headers=headers, params={"limit": 1, "sort_by": "email", "sort_direction": "asc"}
            ).json()
            assert unfiltered["records"][0]["uid"] == actor.get_uid()
            bounds = {
                "last_signed_in_from": "2026-10-24T10:00:00+09:00",
                "last_signed_in_before": "2026-10-24T01:00:01Z",
            }
            filtered = client.get(endpoint, headers=headers, params={"limit": 1, **bounds})
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["total_records"] == 1
            assert filtered.json()["records"][0]["uid"] == target.get_uid()
            assert datetime.fromisoformat(filtered.json()["records"][0]["last_signed_in_at"]) == datetime(
                2026, 10, 24, 1, tzinfo=timezone.utc
            )
            assert (
                client.get(endpoint, headers=headers, params={"page": 2, "limit": 1, **bounds}).json()["records"] == []
            )
            assert (
                client.get(endpoint, headers=headers, params={"query": "HTTP", **bounds}).json()["total_records"] == 0
            )
            assert (
                client.get(endpoint, headers=headers, params={"account_status": "inactive", **bounds}).json()[
                    "total_records"
                ]
                == 0
            )
            business_bounds = {
                "last_business_activity_from": "2026-10-24T11:00:00+09:00",
                "last_business_activity_before": "2026-10-24T02:00:01Z",
            }
            business_page = client.get(endpoint, headers=headers, params={"limit": 1, **bounds, **business_bounds})
            assert business_page.status_code == 200, business_page.text
            assert business_page.json()["total_records"] == 1
            business_record = business_page.json()["records"][0]
            assert business_record["uid"] == target.get_uid()
            assert datetime.fromisoformat(business_record["latest_business_activity_at"]) == datetime(
                2026, 10, 24, 2, tzinfo=timezone.utc
            )
            business_detail = client.get(f"/settings/users/{target.get_uid()}", headers=headers)
            assert (
                business_detail.json()["user"]["latest_business_activity_at"]
                == business_record["latest_business_activity_at"]
            )
            assert (
                client.get(endpoint, headers=headers, params={"page": 2, "limit": 1, **business_bounds}).json()[
                    "records"
                ]
                == []
            )
            assert (
                client.get(
                    endpoint, headers=headers, params={"last_business_activity_before": "2026-10-24T02:00:00Z"}
                ).json()["total_records"]
                == 0
            )
            target_access, target_refresh = AuthSecurity.authenticate(target.id)
            target_url = f"/settings/users/{target.get_uid()}"
            assert client.put(target_url, json={"firstname": "Changed"}, headers=headers).status_code == 403
            assert client.delete(target_url, headers=headers).status_code == 403
            with engine.begin() as db:
                db.execute(
                    update(SettingRole)
                    .where(SettingRole.id == role.id)
                    .values(actions=["user_read", "user_update", "user_delete"])
                )
            # A sole administrator must not remove their own administrator access.
            self_demoted = client.put(f"/settings/users/{actor.get_uid()}", json={"is_admin": False}, headers=headers)
            assert self_demoted.status_code == 409, self_demoted.text
            assert self_demoted.json()["code"] == "OP1005"
            with engine.connect() as db:
                assert db.execute(select(User.is_admin).where(User.id == actor.id)).scalar_one() is True
            assert client.get(endpoint, headers=headers).status_code == 200
            assert UserPublisher.updated.call_count == 0
            assert AppSettingPublisher.user_updated.call_count == 0
            self_unchanged = client.put(f"/settings/users/{actor.get_uid()}", json={"is_admin": True}, headers=headers)
            assert self_unchanged.status_code == 200, self_unchanged.text
            # An authorized administrator may still demote another administrator.
            with engine.begin() as db:
                db.execute(update(User).where(User.id == target.id).values(is_admin=True))
            changed = client.put(
                target_url, json={"firstname": "Changed", "activate": False, "is_admin": False}, headers=headers
            )
            assert changed.status_code == 200, changed.text
            with TestClient(app) as target_client:
                target_client.cookies.set(Env.REFRESH_TOKEN_NAME, target_refresh)
                assert (
                    target_client.get(endpoint, headers={"Authorization": f"Bearer {target_access}"}).status_code == 401
                )
            page = client.get(
                endpoint, headers=headers, params={"query": target.get_uid(), "account_status": "inactive"}
            )
            assert page.status_code == 200, page.text
            assert page.json()["total_records"] == 1
            assert page.json()["records"][0]["firstname"] == "Changed"
            assert page.json()["records"][0]["account_status"] == "inactive"
            assert page.json()["records"][0]["is_admin"] is False
            detail = client.get(target_url, headers=headers)
            assert detail.status_code == 200, detail.text
            assert detail.json()["user"] == page.json()["records"][0]
            assert detail.json()["user"]["last_signed_in_at"] is not None
            assert (
                client.get(
                    endpoint, headers=headers, params={"query": target.get_uid(), "account_status": "active"}
                ).json()["total_records"]
                == 0
            )
            assert client.delete(f"/settings/users/{actor.get_uid()}", headers=headers).status_code == 409
            deleted = client.delete(target_url, headers=headers)
            assert deleted.status_code == 200, deleted.text
            assert client.get(target_url, headers=headers).status_code == 404
            assert (
                client.get(endpoint, headers=headers, params={"query": target.get_uid()}).json()["total_records"] == 0
            )
            with engine.connect() as db:
                assert db.execute(select(User.deleted_at).where(User.id == target.id)).scalar_one() is not None
                assert db.execute(select(Card.created_by_user_id).where(Card.id == card.id)).scalar_one() == target.id
                assert (
                    db.execute(select(CardComment.user_id).where(CardComment.id == comment.id)).scalar_one()
                    == target.id
                )
                assert (
                    db.execute(select(UserSignInHistory.user_id).where(UserSignInHistory.id == history.id)).scalar_one()
                    == target.id
                )
            assert client.put(target_url, json={"firstname": "Resurrected"}, headers=headers).status_code == 404
            with DbSession.atomic() as db:
                bulk_target = User(
                    firstname="Bulk",
                    lastname="Author",
                    username="bulk-author",
                    email="bulk@example.invalid",
                    password="fixture-only",
                    activated_at=SafeDateTime.now(),
                )
                db.insert(bulk_target)
                bulk_comment = CardComment(card_id=card.id, user_id=bulk_target.id)
                db.insert(bulk_comment)
            bulk_deleted = client.request(
                "DELETE",
                "/settings/users",
                headers=headers,
                json={"user_uids": [actor.get_uid(), bulk_target.get_uid()]},
            )
            assert bulk_deleted.status_code == 200, bulk_deleted.text
            remaining = client.get(endpoint, headers=headers).json()
            assert remaining["total_records"] == 1
            assert remaining["records"][0]["uid"] == actor.get_uid()
            with engine.connect() as db:
                assert db.execute(select(User.deleted_at).where(User.id == actor.id)).scalar_one() is None
                assert db.execute(select(User.deleted_at).where(User.id == bulk_target.id)).scalar_one() is not None
                assert (
                    db.execute(select(CardComment.user_id).where(CardComment.id == bulk_comment.id)).scalar_one()
                    == bulk_target.id
                )
            assert AppSettingPublisher.selected_users_deleted.call_args.args[0] == [bulk_target.get_uid()]
            assert UserPublisher.deactivated.call_count == 1
            assert UserPublisher.deleted.call_count == 2

            with DbSession.atomic() as db:
                owner = User(
                    firstname="Organization",
                    lastname="Owner",
                    username="org-owner",
                    email="owner@example.invalid",
                    password="fixture-only",
                    activated_at=SafeDateTime.now(),
                )
                bystander = User(
                    firstname="Ordinary",
                    lastname="User",
                    username="org-bystander",
                    email="bystander@example.invalid",
                    password="fixture-only",
                    activated_at=SafeDateTime.now(),
                )
                db.insert(owner)
                db.insert(bystander)
                organization = Organization(name="Preserved tenant", slug="preserved-tenant", owner_user_id=owner.id)
                db.insert(organization)
            owner_url = f"/settings/users/{owner.get_uid()}"
            renamed_owner = client.put(owner_url, json={"firstname": "Renamed owner"}, headers=headers)
            assert renamed_owner.status_code == 200, renamed_owner.text
            for publisher in (
                UserPublisher.updated,
                UserPublisher.deactivated,
                UserPublisher.deleted,
                AppSettingPublisher.user_updated,
                AppSettingPublisher.selected_users_deleted,
            ):
                publisher.reset_mock()
            for organization_active in (True, False):
                with engine.begin() as db:
                    db.execute(
                        update(Organization)
                        .where(Organization.id == organization.id)
                        .values(is_active=organization_active)
                    )
                blocked = (
                    client.put(owner_url, json={"activate": False}, headers=headers),
                    client.delete(owner_url, headers=headers),
                    client.request(
                        "DELETE",
                        "/settings/users",
                        headers=headers,
                        json={"user_uids": [bystander.get_uid(), owner.get_uid()]},
                    ),
                )
                assert [response.status_code for response in blocked] == [409, 409, 409]
                assert all(response.json()["code"] == "OP1004" for response in blocked)
                with engine.connect() as db:
                    assert db.execute(select(User.activated_at).where(User.id == owner.id)).scalar_one() is not None
                    assert db.execute(select(User.deleted_at).where(User.id == owner.id)).scalar_one() is None
                    assert db.execute(select(User.deleted_at).where(User.id == bystander.id)).scalar_one() is None
                    assert (
                        db.execute(
                            select(Organization.owner_user_id).where(Organization.id == organization.id)
                        ).scalar_one()
                        == owner.id
                    )
            for publisher in (
                UserPublisher.updated,
                UserPublisher.deactivated,
                UserPublisher.deleted,
                AppSettingPublisher.user_updated,
                AppSettingPublisher.selected_users_deleted,
            ):
                assert publisher.call_count == 0
            # Fixture ownership transfer: normal lifecycle operations become available afterward.
            with engine.begin() as db:
                db.execute(
                    update(Organization).where(Organization.id == organization.id).values(owner_user_id=actor.id)
                )
            assert client.put(owner_url, json={"activate": False}, headers=headers).status_code == 200
            with pytest.raises(ValueError, match="owner must be an active account"):
                DomainService().organization.create(owner, "Inactive owner", slug="inactive-owner")
            assert client.delete(owner_url, headers=headers).status_code == 200
            with pytest.raises(ValueError, match="owner must be an active account"):
                DomainService().organization.create(owner, "Deleted owner", slug="deleted-owner")
            # An existing inactive tenant remains canonical after ownership transfer.
            with pytest.raises(ValueError, match="instance tenant already exists"):
                DomainService().organization.create(actor, "Active owner", slug="active-owner")
            reactivated = DomainService().organization.set_active(actor, organization, True)
            assert reactivated.owner_user_id == actor.id
            with engine.connect() as db:
                assert (
                    db.execute(
                        select(Organization.owner_user_id).where(Organization.id == organization.id)
                    ).scalar_one()
                    == actor.id
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_latest_successful_sign_in_range_filters_before_pagination(tmp_path, monkeypatch, backend):
    database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if backend == "postgresql" and not database_url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
    engine = create_engine(database_url if backend == "postgresql" else f"sqlite:///{tmp_path / 'range.sqlite'}")
    models = (User, UserProfile, UserSignInHistory, ProjectActivity, ProjectWikiActivity)
    tables = _create_settings_tables(engine, models)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    start = datetime(2026, 10, 24, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    users = []
    try:
        with DbSession.atomic() as db:
            for index in range(6):
                user = User(
                    firstname="Range",
                    lastname=str(index),
                    username=f"range-{index}",
                    email=f"range-{index}@example.invalid",
                    password="fixture-only",
                    activated_at=start if index != 4 else None,
                )
                db.insert(user)
                users.append(user)
            for index, success, at in (
                (0, True, start - timedelta(days=1)),
                (0, False, start),
                (1, True, start),
                (1, False, end + timedelta(days=1)),
                (2, True, start),
                (2, True, end),
                (4, True, start + timedelta(minutes=30)),
                (5, True, start + timedelta(minutes=45)),
            ):
                db.insert(UserSignInHistory(user_id=users[index].id, is_success=success, signed_in_at=at))
        repository = UserRepository(lambda _: None, lambda _: None)
        roles = SimpleNamespace(
            **{name: SimpleNamespace(get_list=Mock(return_value=[])) for name in ("api_key", "setting", "mcp")}
        )
        service = UserService(lambda _: None, lambda _: None, SimpleNamespace(user=repository, role=roles))
        args = dict(last_signed_in_from=start, last_signed_in_before=end, sort_by="email", sort_direction="asc")
        assert (
            service.get_api_list_in_settings(limit=1, sort_by="email", sort_direction="asc")[0]["uid"]
            == users[0].get_uid()
        )
        expected = [users[index].get_uid() for index in (1, 4, 5)]
        pages = [service.get_api_list_in_settings(offset=offset, limit=1, **args) for offset in range(4)]
        assert [page[0]["uid"] for page in pages[:3]] == expected
        assert pages[3] == []
        bounds = dict(last_signed_in_from=start, last_signed_in_before=end)
        assert service.count_in_settings(**bounds) == 3
        assert service.count_in_settings(last_signed_in_from=start) == 4
        assert service.count_in_settings(last_signed_in_before=end) == 4
        assert service.count_in_settings(account_status="inactive", **bounds) == 1
        assert service.count_in_settings(query="RANGE", account_status="active", **bounds) == 2
        assert service.count_in_settings(column_filters={"email": "range-1"}, **bounds) == 1
        assert service.count_in_settings(exclude_user_uid=users[1].get_uid(), **bounds) == 2
        row = pages[0][0]
        assert row["last_signed_in_at"] == start
        assert row["account_status"] == "active"
        assert row["setting_role_actions"] == [] and row["api_key_role_actions"] == [] and row["mcp_role_actions"] == []
    finally:
        User.__table__.metadata.drop_all(engine, tables=list(tables))
        engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_business_activity_uses_latest_persisted_actor_mutation(tmp_path, monkeypatch, backend):
    from langboard.routes.settings.UserSettingsApi import get_user_in_settings
    from langboard_shared.domain.models import Bot, Project, ProjectWiki, UserActivity
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
    from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
    from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType
    from langboard_shared.domain.models.UserActivity import UserActivityType
    from langboard_shared.tasks.activities.utils.ActivityTaskHelper import ActivityTaskHelper

    database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if backend == "postgresql" and not database_url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
    engine = create_engine(database_url if backend == "postgresql" else f"sqlite:///{tmp_path / 'business.sqlite'}")
    models = (
        User,
        Bot,
        Project,
        ProjectWiki,
        UserProfile,
        UserSignInHistory,
        ProjectActivity,
        ProjectWikiActivity,
        UserActivity,
    )
    tables = _create_settings_tables(engine, models)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    start = datetime(2026, 10, 24, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    users = []
    with DbSession.atomic() as db:
        for index in range(6):
            user = User(
                firstname="Business",
                lastname=str(index),
                username=f"business-{index}",
                email=f"business-{index}@example.invalid",
                password="fixture-only",
                activated_at=start,
                deleted_at=start if index == 5 else None,
            )
            db.insert(user)
            users.append(user)
        project = Project(title="Business fixture", owner_id=users[3].id)
        db.insert(project)
        wiki = ProjectWiki(title="Activity wiki", project_id=project.id)
        db.insert(wiki)
        bot = Bot(
            name="Activity fixture",
            bot_uname="activity-fixture",
            app_api_token="fixture-only",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(bot)
        # A resource owner or name embedded in history must never become its actor.
        for actor, kind, at in (
            (users[0], ProjectActivityType.CardCreated, start - timedelta(days=1)),
            (users[0], ProjectActivityType.CardUpdated, start),
            (users[0], ProjectActivityType.ProjectInvitedUserAccepted, end + timedelta(days=1)),
            (users[1], ProjectActivityType.CardCommentAdded, start),
            (users[1], ProjectActivityType.CardDeleted, end),
            (users[4], ProjectActivityType.ProjectInvitedUserAccepted, end),
            (users[5], ProjectActivityType.CardCreated, start),
            (bot, ProjectActivityType.CardUpdated, end + timedelta(days=2)),
        ):
            ActivityTaskHelper(ProjectActivity).record(
                actor, {"subject_user_id": int(users[3].id)}, project_id=project.id, activity_type=kind, created_at=at
            )
        for kind, at in (
            (ProjectWikiActivityType.WikiCreated, start - timedelta(days=1)),
            (ProjectWikiActivityType.WikiUpdated, start + timedelta(minutes=30)),
            (ProjectWikiActivityType.WikiAssigneesUpdated, end + timedelta(days=1)),
        ):
            ActivityTaskHelper(ProjectWikiActivity).record(
                users[2], {}, project_id=project.id, project_wiki_id=wiki.id, activity_type=kind, created_at=at
            )
        ActivityTaskHelper(UserActivity).record(users[3], {}, activity_type=UserActivityType.Activated, created_at=end)
        db.insert(UserSignInHistory(user_id=users[3].id, is_success=True, signed_in_at=end))
        db.insert(UserSignInHistory(user_id=users[0].id, is_success=True, signed_in_at=start - timedelta(days=3)))
    repository = UserRepository(lambda _: None, lambda _: None)
    roles = SimpleNamespace(
        **{name: SimpleNamespace(get_list=Mock(return_value=[])) for name in ("api_key", "setting", "mcp")}
    )
    service = UserService(lambda _: None, lambda _: None, SimpleNamespace(user=repository, role=roles))
    try:
        records = service.get_api_list_in_settings(sort_by="email", sort_direction="asc")
        assert [row["latest_business_activity_at"] for row in records] == [
            start,
            end,
            start + timedelta(minutes=30),
            None,
            None,
        ]
        assert records[0]["last_signed_in_at"] == start - timedelta(days=3)
        assert records[3]["last_signed_in_at"] == end
        # Preserve the original repository triple contract.
        assert all(len(row) == 3 for row in repository.get_all_with_profile_in_settings())
        bounds = dict(last_business_activity_from=start, last_business_activity_before=end)
        pages = [
            service.get_api_list_in_settings(offset=i, limit=1, sort_by="email", sort_direction="asc", **bounds)
            for i in range(3)
        ]
        assert [page[0]["uid"] for page in pages[:2]] == [users[0].get_uid(), users[2].get_uid()]
        assert pages[2] == []
        assert service.count_in_settings(**bounds) == 2
        assert service.count_in_settings(last_business_activity_from=end) == 1
        assert service.count_in_settings(last_business_activity_before=end) == 2
        assert service.count_in_settings(exclude_user_uid=users[0].get_uid(), **bounds) == 1
        assert service.count_in_settings(column_filters={"email": "business-2"}, **bounds) == 1
        assert service.count_in_settings(last_signed_in_from=end, **bounds) == 0
        api_service = SimpleNamespace(user=service)
        response = get_users_page_in_settings(
            last_business_activity_from="2026-10-24T10:00:00+09:00",
            last_business_activity_before="2026-10-24T11:00:00+09:00",
            service=api_service,
        )
        payload = json.loads(response.body)
        assert payload["total_records"] == 2
        assert {row["uid"] for row in payload["records"]} == {users[0].get_uid(), users[2].get_uid()}
        detail = json.loads(get_user_in_settings(user_uid=users[2].get_uid(), service=api_service).body)["user"]
        assert detail["latest_business_activity_at"] == next(
            row["latest_business_activity_at"] for row in payload["records"] if row["uid"] == users[2].get_uid()
        )
    finally:
        User.__table__.metadata.drop_all(engine, tables=list(tables))
        engine.dispose()
