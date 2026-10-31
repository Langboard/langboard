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
from langboard_shared.domain.models import (
    Card,
    SettingRole,
    WorkflowActionRun,
    WorkflowEffectRun,
    WorkflowRule,
    WorkflowRuleAction,
    WorkflowRuleCondition,
    WorkflowRuleRun,
)
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
    for model in (WorkflowEffectRun, WorkflowRuleRun, WorkflowActionRun, WorkflowRule, WorkflowRuleCondition, WorkflowRuleAction):
        model.__table__.create(engine, checkfirst=True)
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
            run = WorkflowRuleRun(
                rule_id=100, rule_revision=1, event_id=f"event-{i}", event_type="stage_entered",
                residency=SafeDateTime.now().isoformat(), status="succeeded",
                stage_id=stages[0].id, stage_revision=stages[0].edit_revision(),
                project_id=card.project_id, card_id=card.id, actor_id=2, actor_type="user",
            )
            db.insert(run)
            db.insert(WorkflowActionRun(rule_run_id=run.id, action_id=200, kind="timers.stop",
                executor_id=2, executor_type="event_actor", status="succeeded",
                idempotency_key=f"history-{i}", result={"completed": 0, "stopped": 1}))
        db.insert(WorkflowRuleRun(
            rule_id=101, rule_revision=2, event_id="other-stage", event_type="stage_entered",
            residency=SafeDateTime.now().isoformat(), status="succeeded",
            stage_id=stages[1].id, stage_revision="b" * 64,
            project_id=10, card_id=cards[0].id, actor_id=2, actor_type="bot",
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
    assert first["legacy_items"] == []
    action = first["items"][0]["actions"][0]
    assert action["kind"] == "timers.stop" and action["status"] == "succeeded"
    assert action["result"] == {"completed": 0, "stopped": 1}
    assert action["executor_type"] == "event_actor"
    api_rows = f.service.get_effect_history(f.user, f.stages[0].get_uid())["items"]
    assert [row["card_uid"] for row in api_rows] == [f.cards[5].get_uid(), f.cards[0].get_uid()]
    other = f.service.get_effect_history(f.user, f.stages[1].get_uid())
    assert len(other["items"]) == 1 and other["items"][0]["actor_type"] == "bot"
    with pytest.raises(ValueError, match="cursor"):
        f.service.get_effect_history(f.user, f.stages[1].get_uid(), before=first["next_cursor"])
    assert set(first["items"][0]) == {
        "uid", "status", "finished_at", "project_uid", "card_uid", "actor_uid", "actor_type",
        "error_code", "stage_revision", "rule_uid", "rule_revision", "event_id", "event_type",
        "occurred_at", "residency", "condition_results", "actions", "configured_by_uid", "rule_snapshot",
    }


def test_history_preserves_run_configuration_revision_after_stage_edit(history):
    f = history
    original_revision = f.stages[0].edit_revision()
    before = f.service.get_effect_history(f.user, f.stages[0].get_uid())["items"]
    assert before and {row["stage_revision"] for row in before} == {original_revision}
    with DbSession.atomic() as db:
        f.stages[0].confirm_descendant_move = True
        f.stages[0].name = "Changed after execution"
        db.update(f.stages[0])
    assert f.stages[0].edit_revision() != original_revision
    after = f.service.get_effect_history(f.user, f.stages[0].get_uid())["items"]
    assert after == before
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(
            WorkflowRuleRun.stage_id == f.stages[0].id,
        )).all()
        assert stored and {row.stage_revision for row in stored} == {original_revision}


@pytest.mark.parametrize("revocation", ["membership", "role", "account", "project"])
@pytest.mark.parametrize("channel", [CollaborationChannel.Api, CollaborationChannel.HumanUI])
def test_history_rechecks_cached_authority(history, revocation, channel):
    f = history
    assert f.service.get_effect_history(f.user, f.stages[0].get_uid(), channel=channel)["items"]
    cached_user = f.user.model_copy(deep=True)
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
    result = f.service.get_effect_history(cached_user, f.stages[0].get_uid(), channel=channel)
    assert result is None if revocation == "account" else result == {"items": [], "legacy_items": [], "next_cursor": None}


@pytest.mark.parametrize("channel", [CollaborationChannel.Api, CollaborationChannel.HumanUI])
def test_history_rechecks_admin_status_after_board_role_revocation(history, channel):
    f = history
    with DbSession.atomic() as db:
        f.user.is_admin = True
        db.update(f.user)
    cached_admin = f.user.model_copy(deep=True)
    before = f.service.get_effect_history(cached_admin, f.stages[0].get_uid(), channel=channel)
    assert before["items"]
    with DbSession.atomic() as db:
        f.role.actions = []
        db.update(f.role)
    # Native board reads allow a current member administrator without a role grant.
    assert f.service.get_effect_history(cached_admin, f.stages[0].get_uid(), channel=channel) == before
    with DbSession.atomic() as db:
        f.user.is_admin = False
        db.update(f.user)
    assert cached_admin.is_admin
    assert f.service.get_effect_history(cached_admin, f.stages[0].get_uid(), channel=channel) == {
        "items": [], "legacy_items": [], "next_cursor": None,
    }


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
        assert {row["stage_revision"] for row in response.json()["items"]} == {f.stages[0].edit_revision()}
        assert [row["card_uid"] for row in response.json()["items"]] == [
            f.cards[5].get_uid(), f.cards[2].get_uid(), f.cards[0].get_uid(),
        ]
        assert client.get(url + "?limit=51", headers=headers).status_code == 400
        with DbSession.atomic() as db:
            f.role.actions = []
            db.update(f.role)
        # Administrator authority replaces the board role, but never membership
        # or card visibility. The settings role is independently required below.
        still_authorized = client.get(url, headers=headers)
        assert still_authorized.status_code == 200
        assert still_authorized.json() == response.json()
        with DbSession.atomic() as db:
            db.delete(f.member)
        revoked = client.get(url, headers=headers)
        assert revoked.status_code == 200
        assert revoked.json() == {"items": [], "legacy_items": [], "next_cursor": None}
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
    row = next(item for item in page["legacy_items"] if item["uid"] == receipt.get_uid())
    assert row["status"] == "uncertain" and row["error_code"] == "lease_expired"
    assert row["profile"] == {"uid": receipt.profile_id.to_short_code(), "display_name": "컴팩터"}
    assert "execution_key" not in row
    with DbSession.use(readonly=False) as db:
        stored = db.exec(SqlBuilder.select.table(WorkflowEffectRun).where(WorkflowEffectRun.id == receipt.id)).first()
        assert stored.status == "running" and stored.error_code is None


def test_legacy_evidence_shares_current_acl_pagination_without_rewrite(history):
    f = history
    receipts = []
    with DbSession.atomic() as db:
        for card in f.cards:
            receipt = WorkflowEffectRun(
                stage_id=f.stages[0].id, stage_key=f.stages[0].key,
                stage_revision="d" * 64, project_id=card.project_id, card_id=card.id,
                actor_id=2, actor_type="user", source="entry",
                effects=["stop_running_timers"], duration_ms=5,
                result={"completed": 0, "stopped": 1},
            )
            db.insert(receipt)
            receipts.append(receipt)
    seen = []
    cursor = None
    while True:
        page = f.service.get_effect_history(f.user, f.stages[0].get_uid(), before=cursor,
                                            limit=1, channel=CollaborationChannel.HumanUI)
        assert len(page["items"]) + len(page["legacy_items"]) == 1
        seen.extend(page["legacy_items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [row["card_uid"] for row in seen] == [f.cards[i].get_uid() for i in (5, 2, 0)]
    assert all(row["stage_revision"] == "d" * 64 and row["result"] == {"completed": 0, "stopped": 1}
               for row in seen)
    with DbSession.atomic() as db:
        db.delete(f.member)
    assert f.service.get_effect_history(f.user, f.stages[0].get_uid()) == {
        "items": [], "legacy_items": [], "next_cursor": None,
    }
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()
        assert {row.id for row in stored} == {row.id for row in receipts}
        assert all(row.stage_revision == "d" * 64 and row.effects == ["stop_running_timers"]
                   and row.result == {"completed": 0, "stopped": 1} for row in stored)
