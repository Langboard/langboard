"""Settings list uses successful login history, never failed attempts or activity."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
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
