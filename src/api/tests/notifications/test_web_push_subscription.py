from base64 import urlsafe_b64encode
from importlib import import_module
from types import SimpleNamespace
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.auth import AuthApi, OidcAuthApi  # noqa: F401
from langboard.routes.notification import WebPushSubscriptionApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import UserWebPushSubscription
from langboard_shared.Env import Env
from src.api.tests.native_fixtures import board  # noqa: F401


def test_browser_push_opt_in_and_signout_are_scoped_to_the_refresh_session(board, monkeypatch):  # noqa: F811
    _, user, *_ = board
    migration = import_module("langboard.migrations.versions.20261125191500-487ac0aea974")
    with DbEngine.get_main_engine().begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    point = (
        ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    public_key = urlsafe_b64encode(point).decode().rstrip("=")
    monkeypatch.setenv("CARD_REMINDER_WEB_PUSH_ENABLED", "false")
    monkeypatch.setenv("CARD_REMINDER_WEB_PUSH_PUBLIC_KEY", public_key)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    monkeypatch.setattr(
        import_module("langboard.middlewares.ApiAuthMiddleware"),
        "DomainService",
        lambda: SimpleNamespace(close=lambda: None),
    )
    path = "/notification/settings/web-push"
    endpoint = "https://fcm.googleapis.com/fcm/send/test-device"
    registration = {"endpoint": endpoint, "keys": {"p256dh": public_key, "auth": urlsafe_b64encode(b"a" * 16).decode()}}
    access, refresh = AuthSecurity.authenticate(user.id)
    second_access, second_refresh = AuthSecurity.authenticate(user.id)
    _, other_refresh = AuthSecurity.authenticate(2)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    second_headers = {
        "Authorization": f"Bearer {second_access}",
        "Cookie": f"{Env.REFRESH_TOKEN_NAME}={second_refresh}",
    }
    with TestClient(app) as client:
        assert client.get(path).status_code in (401, 422)
        assert client.get(path, headers=headers).json() == {
            "available": False,
            "application_server_key": None,
            "subscribed": False,
        }
        assert client.put(path, headers=headers, json=registration).status_code == 404
        monkeypatch.setenv("CARD_REMINDER_WEB_PUSH_ENABLED", "true")
        assert client.get(path, headers=headers).json() == {
            "available": True,
            "application_server_key": public_key,
            "subscribed": False,
        }
        invalid = client.put(path, headers=headers, json={**registration, "endpoint": "https://example.invalid/path"})
        assert invalid.status_code == 400
        assert "example.invalid" not in invalid.text
        assert client.put(path, headers=headers, json=registration).json() == {"subscribed": True}
        assert client.get(path, headers=headers).json()["subscribed"] is True
        assert client.get(path, headers=second_headers).json()["subscribed"] is False
        assert client.put(path, headers=headers, json=registration).json() == {"subscribed": True}
        assert client.put(path, headers=second_headers, json=registration).status_code == 409
        second_registration = {**registration, "endpoint": endpoint + "-second"}
        assert client.put(path, headers=second_headers, json=second_registration).json() == {"subscribed": True}
        assert client.put(
            path, headers={**headers, "Cookie": f"{Env.REFRESH_TOKEN_NAME}={other_refresh}"}, json=registration
        ).status_code in (401, 403)
        assert client.post("/auth/signout", headers=headers).status_code == 202
        assert client.put(path, headers=headers, json=registration).status_code == 401
        with DbSession.use(readonly=False) as db:
            rows = db.exec(SqlBuilder.select.table(UserWebPushSubscription)).all()
            active = [row for row in rows if not row.signed_out]
            assert len(rows) == 2 and len(active) == 1
            assert all(row.user_id == user.id and endpoint not in (row.encrypted_subscription or "") for row in rows)
            assert all(row.encrypted_subscription is None for row in rows if row.signed_out)
        assert client.delete(path, headers=second_headers).json() == {"subscribed": False}
        assert client.put(path, headers=second_headers, json=second_registration).status_code == 200
        assert client.post("/auth/oidc/logout", headers=second_headers).status_code == 202
        assert client.put(path, headers=second_headers, json=second_registration).status_code == 401
    with DbEngine.get_main_engine().begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
        migration.upgrade()
