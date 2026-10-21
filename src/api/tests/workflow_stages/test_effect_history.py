"""Workflow history applies native current audiences before pagination."""

import importlib
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.settings.WorkflowStageSettingsApi import get_workflow_effect_history
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, SettingRole, WorkflowEffectRun
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility, CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.WorkflowStageService import WorkflowStageService
from langboard_shared.Env import Env


@pytest.fixture
def history(board, monkeypatch):
    # Exercise the native project-member compatibility policy without requiring an
    # unrelated external employee directory in this card-audience fixture.
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    _, user, project, member, role, columns, stages = board
    engine = DbEngine.get_main_engine()
    Card.__table__.create(engine)
    WorkflowEffectRun.__table__.create(engine)
    cards = []
    with DbSession.atomic() as db:
        for i, (visibility, owner) in enumerate([
            (CardVisibility.Shared, 2), (CardVisibility.Private, 2), (CardVisibility.Private, 1),
            (CardVisibility.Shared, 2), (CardVisibility.Shared, 2), (CardVisibility.Shared, 2),
        ]):
            card = Card(project_id=11 if i == 4 else 10, project_column_id=columns[0].id,
                        title="Evidence", visibility=visibility.value,
                        created_by_user_id=owner,
                        owner_user_id=owner if visibility is CardVisibility.Private else None,
                        deleted_at=SafeDateTime.now() if i == 3 else None,
                        archived_at=SafeDateTime.now() if i == 5 else None)
            db.insert(card)
            cards.append(card)
            db.insert(WorkflowEffectRun(
                stage_id=stages[0].id, stage_key=stages[0].key, stage_revision="a" * 64,
                project_id=card.project_id, card_id=card.id, actor_id=2, actor_type="user",
                source="entry", effects=["stop_running_timers"], duration_ms=5,
                result={"completed": 0, "stopped": 1},
            ))
        db.insert(WorkflowEffectRun(
            stage_id=stages[1].id, stage_key=stages[1].key, stage_revision="b" * 64,
            project_id=10, card_id=cards[0].id, actor_id=2, actor_type="bot", source="entry",
            effects=["complete_checkitems"], duration_ms=1, result={"completed": 1, "stopped": 0},
        ))
    card_service = CardService(lambda _: None, lambda _: None, None)
    service = WorkflowStageService(lambda cls: card_service if cls is CardService else None, lambda _: None, None)
    return SimpleNamespace(service=service, user=user, project=project, member=member, role=role,
                           stages=stages, cards=cards)


def test_history_filters_before_paging_retains_archived_and_separates_workflows(history):
    f = history
    contexts = f.service._get_service(CardService).resolve_work_visibility_contexts(f.user, CollaborationChannel.HumanUI)
    assert set(contexts) == {10}
    first = f.service.get_effect_history(f.user, f.stages[0].get_uid(), limit=1, channel=CollaborationChannel.HumanUI)
    assert [row["card_uid"] for row in first["items"]] == [f.cards[5].get_uid()]
    second = f.service.get_effect_history(f.user, f.stages[0].get_uid(), before=first["next_cursor"], limit=1,
                                          channel=CollaborationChannel.HumanUI)
    assert [row["card_uid"] for row in second["items"]] == [f.cards[2].get_uid()]
    third = f.service.get_effect_history(f.user, f.stages[0].get_uid(), before=second["next_cursor"], limit=1,
                                         channel=CollaborationChannel.HumanUI)
    assert [row["card_uid"] for row in third["items"]] == [f.cards[0].get_uid()]
    assert third["next_cursor"] is None
    api_rows = f.service.get_effect_history(f.user, f.stages[0].get_uid())["items"]
    assert [row["card_uid"] for row in api_rows] == [f.cards[5].get_uid(), f.cards[0].get_uid()]
    other = f.service.get_effect_history(f.user, f.stages[1].get_uid())
    assert len(other["items"]) == 1 and other["items"][0]["actor_type"] == "bot"
    with pytest.raises(ValueError, match="cursor"):
        f.service.get_effect_history(f.user, f.stages[1].get_uid(), before=first["next_cursor"])
    assert set(first["items"][0]) == {
        "uid", "source", "status", "effects", "started_at", "finished_at", "duration_ms", "result",
        "project_uid", "card_uid", "actor_uid", "actor_type", "profile", "error_code",
    }


@pytest.mark.parametrize("revocation", ["membership", "role", "account", "project"])
def test_history_rechecks_cached_authority(history, revocation):
    f = history
    assert f.service.get_effect_history(f.user, f.stages[0].get_uid())["items"]
    with DbSession.atomic() as db:
        if revocation == "membership":
            db.delete(f.member)
        else:
            row = {"role": f.role, "account": f.user, "project": f.project}[revocation]
            if revocation == "role":
                row.actions = []
            elif revocation == "account":
                row.activated_at = None
            else:
                row.deleted_at = SafeDateTime.now()
            db.update(row)
    result = f.service.get_effect_history(f.user, f.stages[0].get_uid())
    assert result is None if revocation == "account" else result == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("limit", [0, 51, True, "20"])
def test_history_rejects_unbounded_pages(history, limit):
    with pytest.raises(ValueError, match="limit"):
        history.service.get_effect_history(history.user, history.stages[0].get_uid(), limit=limit)


def test_authenticated_history_requires_admin_setting_read_and_revalidates_card_role(history, monkeypatch):
    f = history
    engine = DbEngine.get_main_engine()
    SettingRole.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(Cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(Cache, "set", lambda *a, **k: None)
    service = SimpleNamespace(workflow_stage=f.service, close=lambda: None)
    monkeypatch.setattr(importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is get_workflow_effect_history:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(f.user.id)
    url = f"/settings/workflow-stages/{f.stages[0].get_uid()}/history"
    headers = {"Authorization": f"Bearer {access}"}
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        assert client.get(url, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            f.user.is_admin = True
            db.update(f.user)
        assert client.get(url, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            setting_role = SettingRole(user_id=f.user.id, actions=["workflow_stage_read"])
            db.insert(setting_role)
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert len(response.json()["items"]) == 3
        assert client.get(url + "?limit=51", headers=headers).status_code == 400
        with DbSession.atomic() as db:
            f.role.actions = []
            db.update(f.role)
        assert client.get(url, headers=headers).json() == {"items": [], "next_cursor": None}
        with DbSession.atomic() as db:
            setting_role.actions = []
            db.update(setting_role)
        assert client.get(url, headers=headers).status_code == 403


def test_expired_compaction_lease_is_reported_uncertain_without_rewriting_evidence(history):
    from datetime import timedelta

    f = history
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        receipt = WorkflowEffectRun(
            stage_id=f.stages[0].id, stage_key=f.stages[0].key, stage_revision="c" * 64,
            project_id=10, card_id=f.cards[0].id, actor_id=f.user.id, actor_type="user",
            source="manual", effects=["compact_comments"], duration_ms=0, result={}, status="running",
            profile_id=123, profile_name="컴팩터", execution_key="compaction:lost:1",
            lease_expires_at=now - timedelta(seconds=1),
        )
        db.insert(receipt)
    page = f.service.get_effect_history(f.user, f.stages[0].get_uid(), channel=CollaborationChannel.HumanUI)
    row = next(item for item in page["items"] if item["uid"] == receipt.get_uid())
    assert row["status"] == "uncertain" and row["error_code"] == "lease_expired"
    assert row["profile"] == {"uid": receipt.profile_id.to_short_code(), "display_name": "컴팩터"}
    assert "execution_key" not in row
    with DbSession.use(readonly=False) as db:
        stored = db.exec(SqlBuilder.select.table(WorkflowEffectRun).where(WorkflowEffectRun.id == receipt.id)).first()
        assert stored.status == "running" and stored.error_code is None
