"""Settings list uses successful login history, never failed attempts or activity."""

from types import SimpleNamespace
from unittest.mock import Mock
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
    finally:
        engine.dispose()
