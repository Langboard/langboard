"""Independent authority remains bounded after grants, role changes and revocation."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.board import BoardCompactorGrantApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import Card, InternalBot, Project, User
from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services.CompactorBoardAuthority import (
    CompactorAuthorityError,
    get_compactor_board_grant,
    require_compactor_card,
    set_compactor_board_grant,
)
from langboard_shared.Env import Env


@pytest.fixture
def authority(compactions):
    _, actor, card, profile, *_ = compactions
    CompactorBoardGrant.__table__.create(DbEngine.get_main_engine())
    with DbSession.atomic() as db:
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
        project.owner_id = actor.id
        db.update(project)
    return actor, card, profile


def change(actor, card, profile, **overrides):
    values = {"can_read": True, "can_compact": True, "can_archive": False, **overrides}
    return set_compactor_board_grant(actor, card.project_id, profile.id, **values)


def allowed(card, profile, action="compact"):
    with DbSession.atomic() as db:
        return require_compactor_card(db, card.project_id, profile.id, card.id, action=action)


def test_no_implicit_authority_and_permissions_are_independent(authority):
    actor, card, profile = authority
    assert not get_compactor_board_grant(actor, card.project_id, profile.id)["can_read"]
    with pytest.raises(CompactorAuthorityError):
        allowed(card, profile)
    response = change(actor, card, profile)
    assert response == {"project_uid": card.project_id.to_short_code(), "profile_uid": profile.get_uid(),
                        "can_read": True, "can_compact": True, "can_archive": False}
    assert allowed(card, profile)[1].id == profile.id
    with pytest.raises(CompactorAuthorityError):
        allowed(card, profile, "archive")
    change(actor, card, profile, can_archive=True)
    assert allowed(card, profile, "archive")[2].id == card.id
    change(actor, card, profile, can_read=False, can_compact=False, can_archive=False)
    with pytest.raises(CompactorAuthorityError):
        allowed(card, profile)


@pytest.mark.parametrize("mutation", ["private", "deleted", "moved", "linked", "profile_deleted", "profile_changed", "board_deleted"])
def test_current_card_and_profile_are_rechecked(authority, mutation):
    actor, card, profile = authority
    change(actor, card, profile)
    assert allowed(card, profile)
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        bot = db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.id == profile.id)).first()
        if mutation == "private":
            current = Card(project_id=card.project_id, project_column_id=card.project_column_id,
                           title="Private original", visibility="PRIVATE", owner_user_id=actor.id,
                           created_by_user_id=actor.id)
            db.insert(current)
            card = current
        elif mutation == "deleted":
            current.deleted_at = current.created_at
        elif mutation == "moved":
            destination = Project(owner_id=actor.id, title="Another board")
            db.insert(destination)
            current.project_id = destination.id
        elif mutation == "linked":
            current.source_type, current.source_uid = "project_wiki", "source"
        elif mutation == "profile_deleted":
            bot.deleted_at = bot.created_at
        elif mutation == "profile_changed":
            bot.bot_type = InternalBotType.EditorChat
        else:
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
            project.deleted_at = project.created_at
            db.update(project)
        db.update(current)
        db.update(bot)
    with pytest.raises(CompactorAuthorityError):
        allowed(card, profile)


def test_grant_does_not_reuse_or_depend_on_saving_users_authority(authority):
    actor, card, profile = authority
    change(actor, card, profile)
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)).first()
        current.activated_at = None
        db.update(current)
    assert allowed(card, profile)
    with pytest.raises(CompactorAuthorityError):
        change(actor, card, profile)


def test_non_owner_cannot_self_grant_and_invalid_permissions_are_rejected(authority):
    actor, card, profile = authority
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)).first()
        current.is_admin = False
        db.update(current)
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
        project.owner_id += 1
        db.update(project)
    with pytest.raises(CompactorAuthorityError):
        change(actor, card, profile)
    with pytest.raises(CompactorAuthorityError):
        get_compactor_board_grant(actor, card.project_id, profile.id)
    for overrides in ({"can_read": False}, {"can_archive": 1}):
        with pytest.raises(CompactorAuthorityError):
            change(actor, card, profile, **overrides)


def test_grant_rolls_back_with_enclosing_transaction(authority):
    actor, card, profile = authority
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            change(actor, card, profile)
            raise RuntimeError("rollback")
    with pytest.raises(CompactorAuthorityError):
        allowed(card, profile)


def test_authenticated_api_grants_revokes_and_rechecks_owner(authority, monkeypatch):
    actor, card, profile = authority
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "compactor-grant-http-fixture-secret-32bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    endpoint = f"/board/{card.project_id.to_short_code()}/compactor/{profile.get_uid()}/grant"
    access, refresh = AuthSecurity.authenticate(actor.id)
    payload = {"can_read": True, "can_compact": True, "can_archive": False}
    with TestClient(app) as client:
        assert client.put(endpoint, json=payload).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        assert client.put(endpoint, json={**payload, "can_archive": "false"}, headers=headers).status_code == 400
        result = client.put(endpoint, json=payload, headers=headers)
        assert result.status_code == 200, result.text
        assert result.headers["Cache-Control"] == "no-store"
        assert client.get(endpoint, headers=headers).json() == result.json()
        assert "private-fixture-credential" not in result.text
        assert allowed(card, profile)
        assert client.put(endpoint, json=dict.fromkeys(payload, False), headers=headers).status_code == 200
        with pytest.raises(CompactorAuthorityError):
            allowed(card, profile)
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)).first()
            current.is_admin = False
            db.update(current)
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
            project.owner_id += 1
            db.update(project)
        assert client.put(endpoint, json=payload, headers=headers).status_code == 403
