"""Bounded unread producer: native counts, current authority and opaque paging."""
import base64
import json
from datetime import timedelta
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, ProjectAssignedUser, ProjectColumn, UserCardReadState
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from sqlalchemy import event, update
from test_card_change_counts import counts as counts_fixture


counts = counts_fixture


def read(f, **kwargs):
    return f.cards.get_unread_card_summaries(f.project, f.user, channel=CollaborationChannel.HumanUI, **kwargs)


def test_bounded_body_free_pages_counts_and_stable_ties(counts, monkeypatch):
    f = counts
    monkeypatch.setattr(f.cards, "_resolve_internal_access", lambda _: False)
    with DbSession.atomic() as db:
        for index in range(61):
            row = Card(project_id=f.project.id, project_column_id=f.column.id, title=str(index), visibility="SHARED")
            db.insert(row)
            db.insert(UserCardReadState(user_id=f.user.id, card_id=row.id, seen_change_seq=-1, seen_at=SafeDateTime.now()))
    f.cards.mark_card_changed(f.card, "comment")
    f.cards.mark_card_changed(f.card, "description")
    sql = []
    def capture(_c, _cu, statement, *_):
        sql.append(statement)
    event.listen(f.engine, "before_cursor_execute", capture)
    try:
        first = read(f)
    finally:
        event.remove(f.engine, "before_cursor_execute", capture)
    assert len(first["items"]) == 50 and first["next_cursor"]
    assert first["items"][0]["unread_change_count"] == 2
    assert first["items"][0]["latest_change"]["target_type"] == "description"
    assert first["items"][1]["unread_change_count_status"] == "manual_unread"
    second = read(f, cursor=first["next_cursor"])
    assert len(second["items"]) == 12 and second["next_cursor"] is None
    assert not ({r["uid"] for r in first["items"]} & {r["uid"] for r in second["items"]})
    assert all(set(row) == {"uid", "project_uid", "title", "has_unread_change", "unread_change_count", "unread_change_count_status", "latest_change"} for row in first["items"])
    assert all("card.description" not in statement.lower() and "project_activity" not in statement.lower() for statement in sql)
    assert len([statement for statement in sql if "card_change_receipt" in statement]) == 1
    # Cursor is bound to actor, board and channel, with strict numeric boundaries.
    fields = json.loads(base64.urlsafe_b64decode(first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4)))
    for mutation in ({"context": ["other", "board", "api"]}, {"before": [-1, 1]}, {"before": [True, 1]}, {"before": [1, 0]}):
        malformed = base64.urlsafe_b64encode(json.dumps({**fields, **mutation}).encode()).decode()
        with pytest.raises(ValueError, match="cursor"):
            read(f, cursor=malformed)
    for cursor in ("bad", "W10", "bnVsbA"):
        with pytest.raises(ValueError, match="cursor"):
            read(f, cursor=cursor)


def test_current_acl_archive_legacy_mirrors_and_revocation(counts, monkeypatch):
    f = counts
    monkeypatch.setattr(f.cards, "_resolve_internal_access", lambda _: False)
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        for fields in (
            {"title": "private", "visibility": "PRIVATE", "owner_user_id": f.other.id, "created_by_user_id": f.other.id},
            {"title": "internal", "visibility": "INTERNAL"},
            {"title": "legacy mirror", "source_type": "project_card", "source_uid": f.card.get_uid()},
            {"title": "wiki mirror", "source_type": "project_wiki", "source_uid": f.card.get_uid()},
            {"title": "cold", "archived_at": now - timedelta(days=f.project.archive_visible_days + 1)},
            {"title": "hot", "archived_at": now},
            {"title": "mine", "visibility": "PRIVATE", "owner_user_id": f.user.id, "created_by_user_id": f.user.id},
        ):
            row = Card(project_id=f.project.id, project_column_id=f.column.id, visibility=fields.pop("visibility", "SHARED"), **fields)
            db.insert(row)
            db.insert(UserCardReadState(user_id=f.user.id, card_id=row.id, seen_change_seq=-1, seen_at=now))
    if f.engine.dialect.name == "sqlite":
        # Reproduce rows retained from before the source-completeness constraint.
        with f.engine.begin() as db:
            db.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            db.execute(update(Card).where(Card.title == "legacy mirror").values(source_uid=None))
            db.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    assert {r["title"] for r in read(f)["items"]} == {"hot", "mine"}
    # Revocation is resolved from primary, even with retained User/Project objects.
    with f.engine.begin() as db:
        db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.id == f.member.id))
    assert read(f) is None
    assert f.cards.get_unread_card_summaries(f.project, None) is None


def test_read_state_liveness_and_unknown_history(counts, monkeypatch):
    f = counts
    monkeypatch.setattr(f.cards, "_resolve_internal_access", lambda _: False)
    f.cards.mark_card_changed(f.card, "comment")
    assert read(f)["items"][0]["unread_change_count"] == 1
    f.repo.user_card_read_state.set_read_state(f.user, f.card, True)
    assert read(f)["items"] == []
    f.repo.user_card_read_state.set_read_state(f.user, f.card, False)
    assert read(f)["items"][0]["unread_change_count_status"] == "manual_unread"
    f.repo.user_card_read_state.set_read_state(f.user, f.card, True)
    with f.engine.begin() as db:
        db.execute(update(Card).where(Card.id == f.card.id).values(last_change_seq=f.card.last_change_seq + 1))
    assert read(f)["items"][0]["unread_change_count_status"] == "unknown_history"
    with f.engine.begin() as db:
        db.execute(update(ProjectColumn).where(ProjectColumn.id == f.column.id).values(deleted_at=SafeDateTime.now()))
    assert read(f)["items"] == []


def test_native_http_route_authority_bounds_and_no_store(counts, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board import BoardApi  # noqa: F401 - register native route
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.models import User
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.Env import Env
    f = counts
    User.metadata.create_all(f.engine)
    monkeypatch.setattr(CardService, "_resolve_internal_access", lambda *_: False)
    f.cards.mark_card_changed(f.card, "comment")
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(f.user.id)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    path = f"/board/{f.project.get_uid()}/unread-card-summaries"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["items"][0]["uid"] == f.card.get_uid()
        assert response.json()["items"][0]["unread_change_count"] == 1
        assert "description" not in response.json()["items"][0]
        assert client.get(path, headers=headers, params={"limit": 51}).status_code == 400
        assert client.get(path, headers=headers, params={"cursor": "bad"}).status_code == 400
        with f.engine.begin() as db:
            db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.id == f.member.id))
        assert client.get(path, headers=headers).status_code in (403, 404)
