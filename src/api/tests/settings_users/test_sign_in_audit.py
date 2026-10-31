"""Sign-in audit metadata requires a separate live grant and never returns secrets."""

from datetime import timedelta, timezone
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
from langboard_shared.domain.models import SettingRole, User, UserSignInHistory
from langboard_shared.domain.models.UserSignInHistory import SignInErrorCode
from langboard_shared.Env import Env
from sqlalchemy import event, update
from test_business_activity import business_activity_engine  # noqa: F401


def test_sign_in_audit_native_authority_and_bounded_projection(
    tmp_path,
    monkeypatch,
    business_activity_engine,  # noqa: F811
    request,
):
    engine = business_activity_engine
    User.metadata.create_all(engine, tables=[UserSignInHistory.__table__])
    request.addfinalizer(lambda: User.metadata.drop_all(engine, tables=[UserSignInHistory.__table__]))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    for name, value in {
        "CACHE_DIR": tmp_path / "cache",
        "FULL_ADMIN_ACCESS_EMAILS": [],
        "JWT_SECRET_KEY": "audit-native-http-test-secret-32-bytes",
    }.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, v=value: v))
    now = SafeDateTime.now().replace(microsecond=0)
    with DbSession.atomic() as db:
        actor, target, regular = [
            User(
                email=f"{name}@example.invalid",
                password="private-password",
                firstname=name,
                lastname="Audit",
                is_admin=name != "regular",
                activated_at=now,
            )
            for name in ("actor", "target", "regular")
        ]
        db.insert_all([actor, target, regular])
        grant = SettingRole(user_id=actor.id, actions=["user_read", "user_audit_read"])
        db.insert_all([grant, SettingRole(user_id=regular.id, actions=["user_read", "user_audit_read"])])
        records = [
            UserSignInHistory(
                user_id=target.id,
                signed_in_at=now + timedelta(seconds=index),
                is_success=index % 2 == 0,
                ip_address="192.0.2.123",
                error_code=None if index % 2 == 0 else SignInErrorCode.InvalidPassword,
            )
            for index in range(4)
        ]
        db.insert_all(records)
        db.insert(
            UserSignInHistory(user_id=actor.id, signed_in_at=now + timedelta(days=1), ip_address="private-address")
        )
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    url = f"/settings/users/{target.get_uid()}/sign-in-history"
    statements = []
    with TestClient(app) as client:

        def login(user):
            access, refresh = AuthSecurity.authenticate(user.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            return {"Authorization": f"Bearer {access}"}

        assert client.get(url).status_code == 401
        assert client.get(url, headers=login(regular)).status_code == 403
        headers = login(actor)

        def inspect(**params):
            response = client.get(url, headers=headers, params=params)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert "192.0.2.123" not in response.text and "private-address" not in response.text
            assert "password" not in response.text.replace("invalid_password", "")
            assert "email" not in response.text and "ip_address" not in response.text
            return response.json()

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            first = inspect(page=1, limit=2)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert first["has_more"] is True
        assert [row["event_id"] for row in first["records"]] == [row.get_uid() for row in records[-1:1:-1]]
        assert first["records"][0]["error_code"] == "invalid_password"
        assert first["records"][1]["error_code"] is None
        assert all("ip_address" not in sql for sql in statements if "user_sign_in_history" in sql)
        second = inspect(page=2, limit=2)
        assert second["has_more"] is False and len(second["records"]) == 2
        assert inspect(page=3, limit=2)["records"] == []
        assert all(row["is_success"] for row in inspect(outcome="success")["records"])
        assert all(not row["is_success"] for row in inspect(outcome="failure")["records"])
        start, end = (
            (now + timedelta(seconds=1)).replace(tzinfo=timezone.utc).isoformat(),
            (now + timedelta(seconds=3)).replace(tzinfo=timezone.utc).isoformat(),
        )
        assert len(inspect(occurred_from=start, occurred_before=end)["records"]) == 2
        for params, status in [
            ({"page": 0}, 400),
            ({"limit": 101}, 400),
            ({"outcome": "invalid"}, 400),
            ({"occurred_from": "2026-10-31T00:00:00"}, 400),
            ({"occurred_from": end, "occurred_before": start}, 400),
        ]:
            assert client.get(url, headers=headers, params=params).status_code == status
        assert client.get("/settings/users/00000000000/sign-in-history", headers=headers).status_code == 404
        for actions in (["user_read"], ["user_audit_read"], []):
            with engine.begin() as db:
                db.execute(update(SettingRole).where(SettingRole.id == grant.id).values(actions=actions))
            assert client.get(url, headers=headers).status_code == 403
        with engine.begin() as db:
            db.execute(update(SettingRole).where(SettingRole.id == grant.id).values(actions=["*"]))
        assert inspect()["records"]
        with engine.begin() as db:
            db.execute(update(User).where(User.id == target.id).values(deleted_at=now))
        assert client.get(url, headers=headers).status_code == 404
