"""Settings list uses successful login history, never failed attempts or activity."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.routes.settings.UserSettingsApi import get_users_page_in_settings
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import User, UserProfile, UserSignInHistory
from langboard_shared.domain.services.factory.UserService import UserService
from langboard_shared.infrastructure.repositories.factory.UserRepository import UserRepository
from sqlalchemy import create_engine, event


def test_settings_login_aggregate_preserves_accounts_without_history(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sign-ins.sqlite'}")
    for model in (User, UserProfile, UserSignInHistory):
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
            assert not {"password", "session", "access_token", "refresh_token"} & detail.json()["user"].keys()
            assert client.get("/settings/users/invalid", headers=headers).status_code == 400
            for params in (
                {"page": 0},
                {"page": 1000001},
                {"limit": 0},
                {"limit": 101},
                {"query": "x" * 201},
                {"email": "x" * 201},
                {"firstname": "x" * 201},
                {"lastname": "x" * 201},
                {"account_status": "deleted"},
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
                history = UserSignInHistory(user_id=target.id, is_success=True)
                db.insert(history)
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
