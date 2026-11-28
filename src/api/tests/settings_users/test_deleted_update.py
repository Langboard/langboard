"""A target deleted after lookup must stop the entire administrator update."""

from unittest.mock import Mock
from bcrypt import checkpw
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.settings import UserSettingsApi  # noqa: F401
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Organization, SettingRole, User, UserProfile
from langboard_shared.domain.services.factory.UserService import UserService
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from sqlalchemy import create_engine, select


def test_deleted_target_stops_update_and_password_change(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'deleted-update.sqlite'}")
    for model in (User, UserProfile, SettingRole, Organization):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "deleted-update-test-key-32-bytes-minimum"))
    for publisher, names in (
        (UserPublisher, ("updated", "deactivated", "deleted")),
        (AppSettingPublisher, ("user_updated",)),
    ):
        for name in names:
            monkeypatch.setattr(publisher, name, Mock())
    with DbSession.atomic() as db:
        actor = User(
            email="admin@example.invalid",
            password="initial",
            firstname="Admin",
            lastname="Fixture",
            activated_at=SafeDateTime.now(),
            is_admin=True,
        )
        target = User(
            email="target@example.invalid",
            password="initial",
            firstname="Target",
            lastname="Fixture",
            activated_at=SafeDateTime.now(),
        )
        db.insert_all([actor, target])
        db.insert(SettingRole(user_id=actor.id, actions=["user_read", "user_update"]))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    url = f"/settings/users/{target.get_uid()}"
    try:
        with TestClient(app) as client:
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            normal = client.put(url, headers=headers, json={"firstname": "Normal", "password": "normal-password"})
            assert normal.status_code == 200, normal.text
            with engine.connect() as db:
                before_password = db.execute(select(User.password).where(User.id == target.id)).scalar_one()
                assert db.execute(select(User.firstname).where(User.id == target.id)).scalar_one() == "Normal"
            assert checkpw(b"normal-password", before_password.get_secret_value().encode())
            assert "password" not in UserPublisher.updated.call_args.args[1]
            UserPublisher.updated.reset_mock()
            AppSettingPublisher.user_updated.reset_mock()
            original_change_password = UserService.change_password

            def fail_after_password_write(service, user, password):
                original_change_password(service, user, password)
                raise RuntimeError("Injected password persistence failure")

            with monkeypatch.context() as mutation_patch:
                mutation_patch.setattr(UserService, "change_password", fail_after_password_write)
                failed = client.put(
                    url, headers=headers, json={"firstname": "Rollback", "password": "rollback-password"}
                )
                assert failed.status_code == 500, failed.text
            with engine.connect() as db:
                assert db.execute(select(User.firstname).where(User.id == target.id)).scalar_one() == "Normal"
                assert db.execute(select(User.password).where(User.id == target.id)).scalar_one() == before_password
            UserPublisher.updated.assert_not_called()
            AppSettingPublisher.user_updated.assert_not_called()
            original_lookup = UserService.get_by_id_like

            def delete_after_lookup(service, uid):
                current = original_lookup(service, uid)
                if uid == target.get_uid() and current:
                    # A separate committed delete wins before update's native locked reread.
                    service.delete(current)
                return current

            monkeypatch.setattr(UserService, "get_by_id_like", delete_after_lookup)
            UserPublisher.updated.reset_mock()
            AppSettingPublisher.user_updated.reset_mock()
            raced = client.put(url, headers=headers, json={"firstname": "Stale", "password": "stale-password"})
            with engine.connect() as db:
                deleted_at, firstname, password = db.execute(
                    select(User.deleted_at, User.firstname, User.password).where(User.id == target.id)
                ).one()
            assert deleted_at is not None
            assert firstname == "Normal"
            assert password == before_password, "Rejected stale update must preserve the deleted account's password"
            assert raced.status_code == 404, raced.text
            assert raced.json()["code"] == "NF1004"
            UserPublisher.updated.assert_not_called()
            AppSettingPublisher.user_updated.assert_not_called()
    finally:
        engine.dispose()
