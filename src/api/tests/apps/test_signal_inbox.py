# ruff: noqa: F811
"""Native board visibility, occurrence grouping and current resource authority."""

import pytest
from langboard.apps.GitHubManifest import GitHubManifestUnavailable
from langboard.apps.SignalInbox import list_board_signals
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.services import DomainService
from test_card_signal_projection import scoped  # noqa: F401
from test_dokploy_connection import setup  # noqa: F401
from test_dokploy_signal import selected  # noqa: F401
from test_github_signal import installation, lifecycle, send, signal_storage  # noqa: F401


def inbox(scoped, after=None):
    state, _, _, _ = scoped
    state[0].card = DomainService().card
    return list_board_signals(state[0], state[1][1], state[1][2].get_uid(), after)


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_registered_external_signal_policy_drives_inbox_and_card_evidence(scoped, monkeypatch):
    from langboard_shared.core.caching.Cache import Cache
    from langboard_shared.core.caching.InMemoryCache import InMemoryCache
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    from packaged_monitor import load_monitor
    Monitor = load_monitor(monkeypatch, "example-monitor-provenance.json")
    import hashlib
    import hmac
    import json
    import time
    import langboard.routes.settings.AppConnectionCredentialApi  # noqa: F401
    import langboard.routes.settings.AppInboundConnectionApi as inbound_api
    import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
    import langboard.routes.settings.AppSignalApi as signal_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.domain.models import (
        AppConnection,
        AppConnectionCredential,
        AppDefinition,
        AppResourceBinding,
        BoardAppBinding,
        Project,
        ProjectAssignedUser,
    )
    from langboard_shared.domain.services.AppConnectionAuthentication import (
        revoke_connection_credential,
    )
    from langboard_shared.domain.services.AppConnectionManagement import (
        list_inbound_resources,
    )
    from langboard_shared.domain.services.AppSignalProjection import card_signal_projections
    from langboard_shared.publishers import CardPublisher

    state, card, link, _ = scoped
    state = list(state)
    scoped = (state, card, link, scoped[3])
    actor = state[1][1]
    AppConnectionCredential.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    monkeypatch.setattr(CardPublisher, "app_signal_changed", lambda _: None)
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
    # Match the native role update path; authentication may already cache this actor.
    from langboard_shared.security import Auth
    Auth.reset_user(actor)
    import importlib
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    state[0].close = lambda: None
    monkeypatch.setattr(signal_api, "DomainService", lambda: state[0])
    monkeypatch.setattr(inbound_api, "DomainService", lambda: state[0])
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: state[0])
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    client = TestClient(app, base_url="https://testserver")
    import asyncio
    from types import SimpleNamespace
    from langboard_sdk import HttpTransport
    access, refresh = AuthSecurity.authenticate(actor.id)
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    user_headers = {"Authorization": "Bearer " + access}
    async def sdk_management_request(method, path, **kwargs):
        return client.request(method, path, headers=user_headers, **kwargs)
    monitor = Monitor(HttpTransport(SimpleNamespace(request=sdk_management_request)))
    saved = asyncio.run(monitor.register())
    management_url = "/settings/apps/registry/example-monitor/inbound-connections"
    form = {"app_revision": saved["revision"],
            "organization_uid": state[1][2].organization_id.to_short_code()}
    assert client.post(management_url, json=form).status_code == 401
    assert client.post(management_url, json={**form, "app_revision": "0" * 64}, headers=user_headers).status_code == 409
    inbound = asyncio.run(monitor.connect(saved["revision"], form["organization_uid"]))
    accepted_first = asyncio.run(monitor.consent(state[1][2].get_uid(), saved["revision"]))
    selected_first = asyncio.run(monitor.select(state[1][2].get_uid(), inbound["connection_uid"],
        saved["revision"], accepted_first, "first-issue"))
    from langboard_shared.core.types import SnowflakeID
    with DbSession.atomic() as db:
        state[2] = db.exec(SqlBuilder.select.table(AppConnection).where(
            AppConnection.id == SnowflakeID.from_short_code(inbound["connection_uid"]),
        )).first()
        assert state[2].credential_reference is None
        state[3] = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.id == SnowflakeID.from_short_code(accepted_first["uid"]))).first()
        state[4] = db.exec(SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.id == SnowflakeID.from_short_code(selected_first["resource_uid"]))).first()
        link.resource_id = state[4].id
        link.commit_sha = ""
        link.is_enabled = False
        db.update(link)
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == "example-monitor")).first()
    issued = asyncio.run(monitor.credential(state[2].get_uid()))
    token = issued["token"]
    envelope = {"version": 1, "board_uid": state[1][2].get_uid(), "resource_uid": state[4].get_uid(),
        "app_revision": definition.edit_revision(), "binding_revision": state[3].edit_revision(),
        "event_id": "fixture-external-1", "event_type": "issue.observed", "external_id": "55",
        "outcome": "open", "commit_sha": "", "occurred_at": "2026-10-11T00:00:00Z"}
    def receive(value, *, signature=None, board_uid=None):
        body = json.dumps(value, sort_keys=True).encode()
        stamp = str(int(time.time()))
        digest = hmac.new(token.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"x-langboard-webhook-version": "1", "x-langboard-webhook-timestamp": stamp,
            "x-langboard-webhook-signature": signature or "v1=" + digest}
        return client.post(f"/apps/v1/boards/{board_uid or state[1][2].get_uid()}/signals", content=body,
                           headers={**headers, "Authorization": "Bearer " + token})
    first_response = receive(envelope)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert not first["duplicate"] and receive(envelope).json() == {**first, "duplicate": True}
    import asyncio
    from types import SimpleNamespace
    from langboard_sdk import HttpTransport
    async def sdk_request(method, path, **kwargs):
        return client.request(method, path, **kwargs)
    sdk_receipt = asyncio.run(Monitor.observe(HttpTransport(SimpleNamespace(request=sdk_request)), token, envelope))
    assert sdk_receipt["signal_uid"] == first["signal_uid"] and sdk_receipt["duplicate"]
    if DbEngine.get_main_engine().dialect.name == "postgresql":
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(4)
        def concurrent_delivery(_):
            barrier.wait(timeout=10)
            result = receive({**envelope, "event_id": "concurrent", "external_id": "56"})
            assert result.status_code == 200, result.text
            return result.json()
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(concurrent_delivery, range(4)))
        assert len({receipt["signal_uid"] for receipt in receipts}) == 1
        assert sum(not receipt["duplicate"] for receipt in receipts) == 1
    assert receive(envelope, signature="v1=" + "0" * 64).status_code == 400
    assert receive({**envelope, "outcome": "closed"}).status_code == 409
    assert receive({**envelope, "event_type": "undeclared"}).status_code == 400
    assert receive({**envelope, "board_uid": "foreign"}).status_code == 400
    for change in ({"resource_uid": {}}, {"resource_uid": "../invalid"}, {"app_revision": []},
                   {"occurred_at": "2026-10-11T00:00:00"}, {"version": True}):
        assert receive({**envelope, **change}).status_code == 400
    assert client.post(f"/apps/v1/boards/{state[1][2].get_uid()}/signals", content=b"{}").status_code == 401
    older = receive({**envelope, "event_id": "older", "occurred_at": "2026-10-10T00:00:00Z", "outcome": "closed"})
    assert older.status_code == 200
    result = inbox(scoped)
    assert {row["provider"] for row in result["items"]} == {"example-monitor"}
    assert result["items"][0]["time_basis"] == "observation"
    def select_resource(board_row, binding, external_id, **changes):
        url = (f"/board/{board_row.get_uid()}/settings/apps/example-monitor/"
               f"inbound-connections/{state[2].get_uid()}/resources")
        body = {"app_revision": definition.edit_revision(), "binding_uid": binding.get_uid(),
                "expected_revision": binding.edit_revision(), "resource_type": "issue",
                "external_resource_id": external_id, **changes}
        response = client.put(url, json=body, headers=user_headers)
        assert response.status_code == 200, response.text
        return response.json()

    selected_second = select_resource(state[1][2], state[3], "second-issue")
    assert select_resource(state[1][2], state[3], "second-issue",
                           expected_access_revision=selected_second["access_revision"]) == selected_second
    with DbSession.atomic() as db:
        second_resource = db.exec(SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.id == SnowflakeID.from_short_code(selected_second["resource_uid"]),
        )).first()
    management_rows = list_inbound_resources(state[0].workflow_stage, actor,
        state[1][2].get_uid(), "example-monitor", state[2].id)["items"]
    assert selected_second["resource_uid"] in {row["resource_uid"] for row in management_rows}
    second = receive({**envelope, "resource_uid": second_resource.get_uid(), "external_id": "second-issue"})
    assert second.status_code == 200 and second.json()["signal_uid"] != first["signal_uid"]
    assert {row["resource_uid"] for row in inbox(scoped)["items"]} == {state[4].get_uid(), second_resource.get_uid()}
    with DbSession.atomic() as db:
        other_board = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
        other_board.organization_id = state[1][2].organization_id
        other_board.owner_id = actor.id
        db.update(other_board)
        db.insert(ProjectAssignedUser(project_id=other_board.id, user_id=actor.id))
    other_envelope = {**envelope, "board_uid": other_board.get_uid()}
    assert receive(other_envelope, board_uid=other_board.get_uid()).status_code == 403
    consent_url = f"/board/{other_board.get_uid()}/settings/apps/example-monitor/consent"
    assert client.post(consent_url, json={"app_revision": "0" * 64}, headers=user_headers).status_code == 409
    from langboard_sdk import AppManager
    manager = AppManager(HttpTransport(SimpleNamespace(request=sdk_management_request)), other_board.get_uid())
    draft = asyncio.run(manager.prepare_consent("example-monitor", definition.edit_revision()))
    assert draft["state"] == "disabled" and draft["granted_capabilities"] == []
    assert not draft["stage_transitions_enabled"]
    assert receive(other_envelope, board_uid=other_board.get_uid()).status_code == 403
    assert asyncio.run(manager.prepare_consent("example-monitor", definition.edit_revision())) == draft
    monitor = Monitor(HttpTransport(SimpleNamespace(request=sdk_management_request)))
    accepted = asyncio.run(monitor.consent(other_board.get_uid(), definition.edit_revision()))
    assert not accepted["stage_transitions_enabled"]
    with DbSession.atomic() as db:
        other_binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.id == SnowflakeID.from_short_code(draft["uid"]))).first()
    selected_other = asyncio.run(monitor.select(other_board.get_uid(), state[2].get_uid(),
        definition.edit_revision(), accepted, "other-board-issue"))
    with DbSession.atomic() as db:
        other_resource = db.exec(SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.id == SnowflakeID.from_short_code(selected_other["resource_uid"]),
        )).first()
    other_envelope.update(binding_revision=other_binding.edit_revision(), resource_uid=other_resource.get_uid(),
                          external_id="other-board-issue")
    received_other = receive(other_envelope, board_uid=other_board.get_uid())
    assert received_other.status_code == 200, received_other.text
    assert received_other.json()["signal_uid"] != first["signal_uid"]
    assert receive({**other_envelope, "resource_uid": state[4].get_uid()}, board_uid=other_board.get_uid()).status_code == 403
    assert receive({**envelope, "resource_uid": other_resource.get_uid()}).status_code == 403
    other_rows = list_board_signals(state[0], actor, other_board.get_uid())["items"]
    assert {row["resource_uid"] for row in other_rows} == {other_resource.get_uid()}
    assert other_resource.get_uid() not in {row["resource_uid"] for row in inbox(scoped)["items"]}
    if DbEngine.get_main_engine().dialect.name == "postgresql":
        from langboard.routes.board.SignalInboxApi import create_inbox_card
        from langboard_shared.domain.models import Card, CardAppOwnership, CardChangeReceipt, CardSignalCreation
        from test_signal_card_creation import prepare_native_creation
        _, _, dispatches = prepare_native_creation(state[0], state[1], monkeypatch)
        CardAppOwnership.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
        CardChangeReceipt.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
        # Register the native router after importing the card-creation endpoint.
        creation_app = FastAPI()
        creation_app.include_router(AppRouter.api)
        creation_app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        for route in creation_app.routes:
            if getattr(route, "endpoint", None) is create_inbox_card:
                for dependency in route.dependant.dependencies:
                    if dependency.name == "service":
                        creation_app.dependency_overrides[dependency.call] = lambda: state[0]
        with TestClient(creation_app, base_url="https://testserver") as creation_client:
            creation_client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            url = f"/board/{state[1][2].get_uid()}/signals/inbox/card"
            body = {"connection_uid": state[2].get_uid(), "resource_uid": second_resource.get_uid(),
                    "signal_uid": second.json()["signal_uid"], "project_column_uid": state[1][5][0].get_uid(),
                    "title": "Explicit external observation"}
            created = creation_client.post(url, json=body, headers=user_headers)
            assert created.status_code == 201, created.text
            repeated = creation_client.post(url, json=body, headers=user_headers)
            assert repeated.status_code == 200 and repeated.json() == {**created.json(), "created": False}
            with DbSession.atomic() as db:
                created_card = db.exec(SqlBuilder.select.table(Card).where(
                    Card.id == SnowflakeID.from_short_code(created.json()["card_uid"]))).first()
                assert created_card.archived_at is None
                assert created_card.project_column_id == state[1][5][0].id
                assert db.exec(SqlBuilder.select.table(CardSignalCreation)).all()
                assert db.exec(SqlBuilder.select.table(CardAppOwnership).where(
                    CardAppOwnership.card_id == created_card.id)).first() is None
            assert dispatches == [created.json()["card_uid"]]
            assert card_signal_projections([created_card])[created_card.id][0]["state"] == "failed"
            resolved = state[0].card.resolve_visibility_context(state[1][2].get_uid(), actor)
            native_state = state[0].card.get_work_states([created_card], context=resolved[1])[created_card.id]
            assert native_state["external_signal_evidence"][0]["provider"] == "example-monitor"
            assert native_state["external_signal_evidence"][0]["evidence_kind"] == "external_observation"
            assert native_state["pending_approval_count"] == 0 and native_state["verification"] is None
            assert native_state["external_execution_state"] is None

    from langboard.apps.CardSignal import bind_check
    linked = bind_check(state[0], actor, state[1][2].get_uid(), card.get_uid(),
        state[2].get_uid(), state[4].get_uid(), first["signal_uid"], card.last_change_seq, link.revision)
    assert linked["binding_uid"] == link.get_uid()
    assert all(row["external_id"] != "55" for row in inbox(scoped)["items"])
    proof = card_signal_projections([card])[card.id]
    assert proof[0]["state"] == "failed" and proof[0]["evidence_kind"] == "external_observation"
    from langboard_shared.domain.services.CardWorkState import project_work_state
    work_state = project_work_state(card_uid=card.get_uid(), workflow_stage="active", archived=False,
        linked_resource=False, total=1, completed=0, started=0, paused=0, external_signals=proof,
        workflow_policy={"key": "active", "counts_as_completed": False,
                         "active_queue_policy": "include", "overdue_policy": "normal"})
    assert work_state["completed"] is False and work_state["workflow_stage"] == "active"
    assert work_state["external_execution_state"] is None
    assert work_state["verification"] is None and work_state["pending_approval_count"] == 0
    assert work_state["external_signal_evidence"] == proof
    assert any(row["code"] == "external_observation_failed" for row in work_state["reasons"])
    revoked_resource = select_resource(state[1][2], state[3], state[4].external_resource_id,
        selected=False, expected_access_revision=state[4].access_revision)
    assert revoked_resource["access_state"] == "revoked"
    assert receive({**envelope, "event_id": "resource-revoked"}).status_code == 403
    assert all(row["external_id"] != "55" for row in inbox(scoped)["items"])
    assert card_signal_projections([card]) == {}
    restored_resource = select_resource(state[1][2], state[3], state[4].external_resource_id,
        expected_access_revision=revoked_resource["access_revision"])
    assert restored_resource["access_revision"] == revoked_resource["access_revision"] + 1
    assert card_signal_projections([card])[card.id][0]["state"] == "failed"
    with DbSession.atomic() as db:
        state[3].granted_capabilities = ["signals.read"]
        db.update(state[3])
    assert receive({**envelope, "event_id": "grant-revoked"}).status_code == 403
    with DbSession.atomic() as db:
        state[3].granted_capabilities = ["signals.read", "signals.publish"]
        db.update(state[3])
    with DbSession.atomic() as db:
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == "example-monitor")).first()
        definition.is_enabled = False
        db.update(definition)
    assert inbox(scoped)["items"] == []
    assert card_signal_projections([card]) == {}
    assert receive({**envelope, "event_id": "after-revoke"}).status_code == 403
    with DbSession.atomic() as db:
        definition.is_enabled = True
        db.update(definition)
    from langboard_shared.core.types import SnowflakeID
    revoke_connection_credential(actor, state[2].id, SnowflakeID.from_short_code(issued["credential_uid"]))
    assert receive(envelope).status_code == 403
    assert inbox(scoped)["items"] == []
    assert card_signal_projections([card]) == {}
    with pytest.raises(GitHubManifestUnavailable):
        bind_check(state[0], actor, state[1][2].get_uid(), card.get_uid(),
            state[2].get_uid(), state[4].get_uid(), first["signal_uid"], card.last_change_seq, linked["revision"])
    client.close()


def test_inbox_hides_visible_links_but_not_private_link_existence(scoped):
    state, card, binding, _ = scoped
    send(state)
    assert inbox(scoped)["items"] == []
    with DbSession.use(readonly=False) as db:
        card.visibility = "PRIVATE"
        card.owner_user_id = state[1][1].id
        card.created_by_user_id = state[1][1].id
        db.update(card)
    assert len(inbox(scoped)["items"]) == 1

    with DbSession.use(readonly=False) as db:
        card.visibility = "SHARED"
        card.owner_user_id = None
        db.update(card)
        binding.is_enabled = False
        db.update(binding)
    assert len(inbox(scoped)["items"]) == 1


@pytest.mark.parametrize("admin", [False, True])
def test_inbox_personal_connection_is_private_even_to_board_owner(scoped, admin):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import User

    state, _, binding, _ = scoped
    send(state)
    with DbSession.use(readonly=False) as db:
        state[2].ownership = "personal"
        state[2].organization_id = None
        db.update(state[2])
        binding.is_enabled = False
        db.update(binding)
        owner = db.exec(SqlBuilder.select.table(User).where(User.id == state[1][2].owner_id)).first()
        owner.is_admin = admin
        db.update(owner)
    visible = inbox(scoped)
    assert len(visible["items"]) == 1
    assert owner.id != state[1][1].id
    hidden = list_board_signals(state[0], owner, state[1][2].get_uid())
    assert hidden == {"items": [], "next_cursor": None}
    with pytest.raises(ValueError):
        list_board_signals(state[0], owner, state[1][2].get_uid(), visible["items"][0]["signal_uid"])


def test_inbox_latest_occurrence_conflict_and_bounded_pages(scoped):
    state, _, binding, _ = scoped
    with DbSession.use(readonly=False) as db:
        binding.is_enabled = False
        db.update(binding)
    send(state)
    state[5]["check_run"].update(conclusion="failure", completed_at="2026-10-08T02:00:00Z")
    send(state)
    state[5]["check_run"].update(conclusion="success", completed_at="2026-10-08T01:00:00Z")
    send(state)
    rows = inbox(scoped)["items"]
    assert len(rows) == 1 and rows[0]["outcome"] == "failure" and not rows[0]["conflict"]
    state[5]["check_run"].update(completed_at="2026-10-08T02:00:00Z")
    send(state)
    row = inbox(scoped)["items"][0]
    assert row["conflict"] and row["outcome"] is None
    assert "payload_digest" not in row and "event_id" not in row
    for external_id in range(100, 130):
        state[5]["check_run"].update(id=external_id)
        send(state)
    first = inbox(scoped)
    second = inbox(scoped, first["next_cursor"])
    assert len(first["items"]) == 25 and len(second["items"]) == 6 and second["next_cursor"] is None
    assert not ({row["signal_uid"] for row in first["items"]} & {row["signal_uid"] for row in second["items"]})
    with pytest.raises(ValueError):
        inbox(scoped, "../invalid")


@pytest.mark.parametrize("revocation", ["resource", "secret", "reader"])
def test_inbox_current_revocation(scoped, revocation):
    state, _, binding, _ = scoped
    with DbSession.use(readonly=False) as db:
        binding.is_enabled = False
        db.update(binding)
    send(state)
    assert len(inbox(scoped)["items"]) == 1
    if revocation == "secret":
        state[0].secret_reference.revoke(state[1][1], state[2].credential_reference, 0)
    else:
        with DbSession.use(readonly=False) as db:
            target = state[4] if revocation == "resource" else state[1][1]
            if revocation == "resource":
                target.is_selected = False
            else:
                target.activated_at = None
            db.update(target)
    if revocation == "reader":
        with pytest.raises(GitHubManifestUnavailable):
            inbox(scoped)
    else:
        assert inbox(scoped)["items"] == []


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_inbox_native_http_auth_cursor_and_board_scope(scoped, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board.SignalInboxApi import get_signal_inbox
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    state, _, binding, _ = scoped
    service = state[0]
    service.card = DomainService().card
    service.close = lambda: None
    with DbSession.use(readonly=False) as db:
        binding.is_enabled = False
        db.update(binding)
    send(state)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    for route in app.routes:
        if getattr(route, "endpoint", None) == get_signal_inbox:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    url = f"/board/{state[1][2].get_uid()}/signals/inbox"
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get(url).status_code == 401
        access, refresh = AuthSecurity.authenticate(state[1][1].id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        result = client.get(url, headers=headers)
        assert result.status_code == 200, result.text
        assert len(result.json()["items"]) == 1
        assert client.get(url, params={"after": "../invalid"}, headers=headers).status_code == 400
        assert client.get(url.replace(state[1][2].get_uid(), "nonexistent"), headers=headers).status_code == 404


@pytest.fixture
def dokploy_inbox(selected):
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import (
        Card,
        CardAppSignalBinding,
        EmployeeMembershipPolicy,
        ScimGroup,
        ScimGroupMember,
        UserIdentityLink,
    )

    service, board, *_ = selected[0]
    engine = DbEngine.get_main_engine()
    for model in (Card, CardAppSignalBinding, EmployeeMembershipPolicy, ScimGroup, ScimGroupMember, UserIdentityLink):
        model.__table__.create(engine, checkfirst=True)
    domain = DomainService()
    service.card = domain.card
    try:
        yield selected
    finally:
        domain.close()


def deployment_inbox(selected, after=None):
    service, board, *_ = selected[0]
    return list_board_signals(service, board[1], board[2].get_uid(), after)


def test_dokploy_lifecycle_conflict_and_safe_contract(dokploy_inbox):
    from test_dokploy_signal import refresh

    row = dokploy_inbox[0][4]["rows"][0]
    row["status"] = "running"
    refresh(dokploy_inbox)
    entry = deployment_inbox(dokploy_inbox)["items"][0]
    assert entry["event_type"] == "deployment.queued" and entry["outcome"] == "queued"
    assert entry["can_bind_card"] and entry["commit_sha"] == ""
    assert entry["resource_type"] in {"application", "compose"}
    assert entry["resource_name"] == ("API" if entry["resource_type"] == "application" else "Stack")
    assert "private" not in str(entry)
    row["startedAt"] = "2026-10-08T10:00:10Z"
    refresh(dokploy_inbox)
    assert deployment_inbox(dokploy_inbox)["items"][0]["event_type"] == "deployment.started"
    row["status"] = "done"
    refresh(dokploy_inbox)
    assert deployment_inbox(dokploy_inbox)["items"][0]["outcome"] == "success"
    # Late arrival cannot replace a later occurrence.
    row["status"] = "running"
    refresh(dokploy_inbox)
    assert deployment_inbox(dokploy_inbox)["items"][0]["outcome"] == "success"
    row["status"] = "error"
    refresh(dokploy_inbox)
    entry = deployment_inbox(dokploy_inbox)["items"][0]
    assert entry["conflict"] and entry["outcome"] is None
    row.update(status="cancelled", finishedAt="2026-10-08T10:02:00Z")
    refresh(dokploy_inbox)
    entry = deployment_inbox(dokploy_inbox)["items"][0]
    assert entry["event_type"] == "deployment.cancelled" and entry["outcome"] == "cancelled"
    assert not entry["conflict"]
    assert not {"payload_digest", "event_id", "approval", "verified", "logPath", "errorMessage"} & entry.keys()


@pytest.mark.parametrize(
    "revocation", ["signals", "deployments", "disabled", "secret", "role", "resource", "access", "connection", "member"]
)
def test_dokploy_inbox_current_authority(dokploy_inbox, revocation):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import AppConnection, AppResourceBinding, BoardAppBinding, SecretReference
    from test_dokploy_signal import refresh

    refresh(dokploy_inbox)
    assert len(deployment_inbox(dokploy_inbox)["items"]) == 1
    board = dokploy_inbox[0][1]
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        resource = db.exec(SqlBuilder.select.table(AppResourceBinding)).first()
        connection = db.exec(SqlBuilder.select.table(AppConnection)).first()
        if revocation in {"signals", "deployments"}:
            binding.granted_capabilities = [cap for cap in binding.granted_capabilities if cap != revocation + ".read"]
            db.update(binding)
        elif revocation == "disabled":
            binding.state = "disabled"
            db.update(binding)
        elif revocation == "secret":
            secret = db.exec(SqlBuilder.select.table(SecretReference)).first()
            secret.state = "revoked"
            db.update(secret)
        elif revocation == "role":
            board[4].actions = ["read"]
            db.update(board[4])
        elif revocation == "member":
            db.delete(board[3])
        elif revocation in {"resource", "access"}:
            if revocation == "resource":
                resource.is_selected = False
            else:
                resource.access_state = "revoked"
            db.update(resource)
        else:
            connection.state = "disconnected"
            db.update(connection)
    if revocation == "member":
        with pytest.raises(GitHubManifestUnavailable):
            deployment_inbox(dokploy_inbox)
    else:
        assert deployment_inbox(dokploy_inbox)["items"] == []


def test_dokploy_inbox_bounded_pages_board_provider_and_resource_fences(dokploy_inbox):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import AppResourceBinding, AppSignal, BoardAppBinding
    from test_dokploy_signal import refresh

    state = dokploy_inbox[0][4]
    template = state["rows"][0]
    refresh(dokploy_inbox)
    with DbSession.use(readonly=False) as db:
        from langboard.apps.DokploySignal import normalize_deployment

        resource = db.exec(SqlBuilder.select.table(AppResourceBinding)).first()
        db.insert_all(
            [
                AppSignal(
                    resource_id=resource.id,
                    **normalize_deployment(
                        {**template, "deploymentId": f"dep-page-{number}"},
                        resource.resource_type,
                        resource.external_resource_id,
                    ),
                )
                for number in range(30)
            ]
        )
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        foreign_binding = BoardAppBinding(
            project_id=11, app_key="dokploy", state="enabled", granted_capabilities=binding.granted_capabilities
        )
        db.insert(foreign_binding)
        foreign = AppResourceBinding(
            board_binding_id=foreign_binding.id,
            connection_id=resource.connection_id,
            resource_type=resource.resource_type,
            external_resource_id="foreign",
            access_state="granted",
        )
        parent = AppResourceBinding(
            board_binding_id=binding.id,
            connection_id=resource.connection_id,
            resource_type="environment",
            external_resource_id="env",
            access_state="granted",
        )
        db.insert(foreign)
        db.insert(parent)
        for number, (target, provider, event_type, sha) in enumerate(
            (
                (foreign, "dokploy", "deployment.succeeded", ""),
                (parent, "dokploy", "deployment.succeeded", ""),
                (resource, "github", "check.completed", "a" * 40),
                (resource, "dokploy", "check.completed", ""),
                (resource, "dokploy", "deployment.succeeded", "a" * 40),
            )
        ):
            db.insert(
                AppSignal(
                    resource_id=target.id,
                    provider=provider,
                    event_type=event_type,
                    external_id="excluded",
                    commit_sha=sha,
                    occurred_at="2026-10-08T12:00:00.000000+00:00",
                    outcome="success",
                    event_id=f"excluded-{number}",
                    payload_digest="0" * 64,
                )
            )
    first = deployment_inbox(dokploy_inbox)
    second = deployment_inbox(dokploy_inbox, first["next_cursor"])
    assert len(first["items"]) == 25 and len(second["items"]) == 6 and second["next_cursor"] is None
    assert "excluded" not in str(first) + str(second)
    assert not ({row["signal_uid"] for row in first["items"]} & {row["signal_uid"] for row in second["items"]})
    with pytest.raises(ValueError):
        deployment_inbox(dokploy_inbox, foreign.get_uid())


def test_github_commit_identity_remains_bindable(scoped):
    state, _, binding, _ = scoped
    with DbSession.use(readonly=False) as db:
        binding.is_enabled = False
        db.update(binding)
    send(state)
    state[5]["check_run"].update(head_sha="b" * 40)
    send(state)
    entries = inbox(scoped)["items"]
    assert {entry["commit_sha"] for entry in entries} == {"a" * 40, "b" * 40}
    assert all(entry["can_bind_card"] for entry in entries)


def test_dokploy_deployment_identity_is_resource_scoped_and_visible_links_consumed(dokploy_inbox):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import (
        AppResourceBinding,
        AppSignal,
        BoardAppBinding,
        Card,
        CardAppSignalBinding,
    )
    from test_dokploy_signal import refresh

    refresh(dokploy_inbox)
    board = dokploy_inbox[0][1]
    with DbSession.use(readonly=False) as db:
        original = db.exec(SqlBuilder.select.table(AppResourceBinding)).first()
        original_signal = db.exec(SqlBuilder.select.table(AppSignal)).first()
        sibling = AppResourceBinding(
            board_binding_id=original.board_binding_id,
            connection_id=original.connection_id,
            resource_type=original.resource_type,
            external_resource_id="second-resource",
            access_state="granted",
            is_selected=True,
        )
        db.insert(sibling)
        db.insert(
            AppSignal(
                resource_id=sibling.id,
                provider="dokploy",
                event_type="deployment.failed",
                external_id="dep-1",
                commit_sha="",
                occurred_at=original_signal.occurred_at,
                outcome="failure",
                event_id="second-resource",
                payload_digest="0" * 64,
            )
        )
        card = Card(
            project_id=board[2].id, project_column_id=board[5][0].id, title="No deployment binding", visibility="SHARED"
        )
        db.insert(card)
        # Enabled links consume their exact resource/deployment identity.
        db.insert(
            CardAppSignalBinding(
                card_id=card.id, resource_id=original.id, external_id="dep-1", commit_sha="", source_change_seq=0
            )
        )
    entries = deployment_inbox(dokploy_inbox)["items"]
    assert len(entries) == 1 and entries[0]["outcome"] == "failure"
    assert all(not entry["conflict"] and entry["can_bind_card"] for entry in entries)
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        assert not current.stage_transitions_enabled and current.workflow_mapping == {}
        assert db.exec(SqlBuilder.select.table(Card)).first().last_change_seq == 0


def test_inbox_resource_name_fallback_and_bound():
    from types import SimpleNamespace
    from langboard.apps.SignalInbox import _resource_name

    assert (
        _resource_name(SimpleNamespace(resource_path=[], external_resource_id="deployment-resource"))
        == "deployment-resource"
    )
    assert (
        _resource_name(SimpleNamespace(resource_path=[{"name": "x" * 250}], external_resource_id="fallback"))
        == "x" * 200
    )
    assert (
        _resource_name(SimpleNamespace(resource_path=[{"name": {"env": "private"}}], external_resource_id="fallback"))
        == "fallback"
    )


def test_provider_filter_is_applied_before_cursor_and_page_limit(scoped):
    state, _, binding, _ = scoped
    with DbSession.use(readonly=False) as db:
        binding.is_enabled = False
        db.update(binding)
    send(state)
    state[0].card = DomainService().card
    uid = state[1][2].get_uid()
    github = list_board_signals(state[0], state[1][1], uid, provider="github")
    assert len(github["items"]) == 1
    assert list_board_signals(state[0], state[1][1], uid, provider="dokploy")["items"] == []
    with pytest.raises(ValueError):
        list_board_signals(state[0], state[1][1], uid, github["items"][0]["signal_uid"], provider="dokploy")
    with pytest.raises(ValueError):
        list_board_signals(state[0], state[1][1], uid, provider="unknown")


@pytest.mark.parametrize("change", ["disabled", "capability"])
def test_registry_revocation_hides_inbox_and_invalidates_its_cursor(scoped, change):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import AppDefinition, AppSignal

    state, _, binding, _ = scoped
    with DbSession.atomic() as db:
        binding.is_enabled = False
        db.update(binding)
    send(state)
    rows = inbox(scoped)["items"]
    assert len(rows) == 1
    with DbSession.atomic() as db:
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == "github")).first()
        if change == "disabled":
            definition.is_enabled = False
        else:
            definition.declaration = {"capabilities": ["resources.read"]}
        db.update(definition)
    assert inbox(scoped) == {"items": [], "next_cursor": None}
    with pytest.raises(ValueError, match="Invalid inbox cursor"):
        inbox(scoped, rows[0]["signal_uid"])
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(AppSignal)).all()) == 1


@pytest.mark.parametrize("change", ["disabled", "signals", "deployments"])
def test_dokploy_registry_override_hides_stored_deployments(dokploy_inbox, change):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import AppDefinition, AppSignal
    from test_dokploy_signal import refresh

    board = dokploy_inbox[0][1]
    refresh(dokploy_inbox)
    assert deployment_inbox(dokploy_inbox)["items"]
    capabilities = ["resources.read", "signals.read", "deployments.read"]
    if change != "disabled":
        capabilities.remove("signals.read" if change == "signals" else "deployments.read")
    with DbSession.atomic() as db:
        db.insert(AppDefinition(key="dokploy", approved_by=board[1].id, is_enabled=change != "disabled",
                                declaration={"capabilities": capabilities}))
    assert deployment_inbox(dokploy_inbox) == {"items": [], "next_cursor": None}
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(AppSignal)).all()) == 1


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("trust_change", ["publisher", "panel", "mcp_identity", "authentication_origins", "mcp_origins", "data_origins", "resource_types"])
def test_panel_declared_source_and_resource_revocation_with_stored_signal(scoped, monkeypatch, trust_change):
    import importlib
    from datetime import datetime, timezone
    import langboard.routes.board.BoardSettingApi  # noqa: F401
    import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.apps.GlitchTipSignal import normalize_issue
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board.AppPanelDataApi import read_panel_signals
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.models import (
        AppConnection,
        AppDefinition,
        AppGovernancePolicy,
        AppMcpConsent,
        AppResourceBinding,
        AppSignal,
        BoardAppBinding,
        User,
    )
    from langboard_shared.Env import Env
    from pydantic import SecretStr

    state, card, linked, _ = scoped
    AppMcpConsent.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    service = state[0]
    domain = DomainService()
    service.card = domain.card
    service.close = lambda: None
    reference = service.secret_reference.create(
        state[1][1], "personal", "me", "panel/glitchtip", SecretStr("fixture-panel-token")
    )
    with DbSession.atomic() as db:
        linked.is_enabled = False
        db.update(linked)
        definition = AppDefinition(key="github-panel", approved_by=state[1][1].id, declaration={
            "schema_version": 1, "key": "github-panel", "version": "0.1.1", "name": "GitHub panel",
            "description": "Read approved GitHub signals", "capabilities": ["panels.render", "signals.read"],
            "resource_types": ["repository"], "signal_sources": ["github"],
            "panel": {"url": "https://panel.example.invalid", "name": "GitHub", "icon": "github"},
        })
        db.insert(definition)
        panel_binding = BoardAppBinding(project_id=card.project_id, app_key="github-panel", state="enabled",
            granted_capabilities=["panels.render", "signals.read"])
        db.insert(panel_binding)
        other_connection = AppConnection(
            app_key="glitchtip", owner_id=state[1][1].id, state="connected",
            credential_reference=reference["uri"], instance_url="https://errors.example.invalid",
        )
        db.insert(other_connection)
        other_binding = BoardAppBinding(
            project_id=card.project_id, app_key="glitchtip", state="enabled",
            granted_capabilities=["resources.read", "signals.read"],
        )
        db.insert(other_binding)
        other_resource = AppResourceBinding(
            board_binding_id=other_binding.id, connection_id=other_connection.id,
            resource_type="project", external_resource_id="2", access_state="granted", is_selected=True,
            resource_path=[{"type": "organization", "id": "test-org"},
                           {"type": "project", "id": "2", "slug": "test-project"}],
        )
        db.insert(other_resource)
        observed = normalize_issue({
            "id": "10", "project": {"id": "2", "slug": "test-project"}, "status": "unresolved",
            "firstSeen": "2026-10-08T00:00:00Z", "lastSeen": "2026-10-08T00:01:00Z",
        }, "2", "test-project", datetime(2026, 10, 9, tzinfo=timezone.utc))
        other_signal = AppSignal(resource_id=other_resource.id, event_id="fixture-observation", **observed)
        db.insert(other_signal)
    send(state)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    for route in app.routes:
        if getattr(route, "endpoint", None) == read_panel_signals:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    path = f"/board/{state[1][2].get_uid()}/apps/github-panel/panel/signals"
    form = {"app_revision": definition.edit_revision(), "binding_revision": panel_binding.edit_revision(), "provider": "github"}
    try:
        with TestClient(app, base_url="https://testserver") as client:
            assert client.post(path, json=form).status_code == 401
            access, refresh = AuthSecurity.authenticate(state[1][1].id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            visible = list_board_signals(service, state[1][1], state[1][2].get_uid())
            assert {item["provider"] for item in visible["items"]} == {"github", "glitchtip"}
            with DbSession.atomic() as db:
                other_resource.is_selected = False
                db.update(other_resource)
            assert {item["provider"] for item in inbox(scoped)["items"]} == {"github"}
            with DbSession.atomic() as db:
                assert db.exec(SqlBuilder.select.table(AppSignal).where(AppSignal.id == other_signal.id)).first() is not None
                other_resource.is_selected = True
                db.update(other_resource)
            assert {item["provider"] for item in inbox(scoped)["items"]} == {"github", "glitchtip"}
            result = client.post(path, json=form, headers=headers)
            assert result.status_code == 200, result.text
            items = result.json()["items"]
            assert len(items) == 1 and items[0]["provider"] == "github" and items[0]["external_id"] == "55"
            assert items[0]["connection_uid"] == state[2].get_uid()
            for provider in ("glitchtip", "dokploy"):
                denied = client.post(path, json={**form, "provider": provider, "after": items[0]["signal_uid"]}, headers=headers)
                assert denied.status_code == 404 and "items" not in denied.json()
            assert client.post(path, json={**form, "provider": "glitchtip"}, headers=headers).status_code == 404
            with DbSession.atomic() as db:
                definition.declaration = {**definition.declaration, "signal_sources": ["github", "glitchtip"]}
                db.update(definition)
            assert client.post(path, json=form, headers=headers).status_code == 409
            form["app_revision"] = definition.edit_revision()
            other_result = client.post(path, json={**form, "provider": "glitchtip"}, headers=headers)
            assert other_result.status_code == 200, other_result.text
            # Provider consent alone cannot expand the panel's declared resource types.
            assert other_result.json() == {"items": [], "next_cursor": None}
            assert client.post(path, json={**form, "provider": "glitchtip", "after": other_signal.get_uid()}, headers=headers).status_code == 400
            with DbSession.atomic() as db:
                definition.declaration = {**definition.declaration, "resource_types": ["repository", "project"]}
                db.update(definition)
            assert client.post(path, json=form, headers=headers).status_code == 409
            form["app_revision"] = definition.edit_revision()
            other_result = client.post(path, json={**form, "provider": "glitchtip"}, headers=headers)
            assert other_result.status_code == 200, other_result.text
            assert [item["signal_uid"] for item in other_result.json()["items"]] == [other_signal.get_uid()]
            assert client.post(path, json={**form, "after": other_signal.get_uid()}, headers=headers).status_code == 400
            with DbSession.atomic() as db:
                panel_binding.granted_capabilities = ["panels.render"]
                db.update(panel_binding)
            assert client.post(path, json=form, headers=headers).status_code == 404
            with DbSession.atomic() as db:
                panel_binding.granted_capabilities = ["panels.render", "signals.read"]
                db.update(panel_binding)
            form["binding_revision"] = panel_binding.edit_revision()
            # A hidden card link cannot reveal private-card existence by suppressing inbox evidence.
            with DbSession.atomic() as db:
                linked.is_enabled = True
                db.update(linked)
            assert client.post(path, json=form, headers=headers).json()["items"] == []
            with DbSession.atomic() as db:
                card.visibility = "PRIVATE"
                card.owner_user_id = 2
                card.created_by_user_id = 2
                db.update(card)
            private_result = client.post(path, json=form, headers=headers)
            assert private_result.status_code == 200 and len(private_result.json()["items"]) == 1
            assert "card_uid" not in private_result.json()["items"][0]
            with DbSession.atomic() as db:
                card.owner_user_id = state[1][1].id
                card.created_by_user_id = state[1][1].id
                db.update(card)
            assert client.post(path, json=form, headers=headers).json()["items"] == []
            with DbSession.atomic() as db:
                linked.is_enabled = False
                db.update(linked)
                state[1][4].actions = []
                db.update(state[1][4])
            assert client.post(path, json=form, headers=headers).status_code == 404
            with DbSession.atomic() as db:
                state[1][4].actions = ["read", "update"]
                db.update(state[1][4])
                policy = AppGovernancePolicy(scope_key="global", mode="disabled")
                db.insert(policy)
            assert client.post(path, json=form, headers=headers).status_code == 404
            with DbSession.atomic() as db:
                policy.mode = "approved_only"
                db.update(policy)
            assert len(client.post(path, json=form, headers=headers).json()["items"]) == 1
            with DbSession.atomic() as db:
                other_connection.state = "revoked"
                db.update(other_connection)
            assert client.post(path, json={**form, "provider": "glitchtip"}, headers=headers).json()["items"] == []
            assert len(client.post(path, json=form, headers=headers).json()["items"]) == 1
            with DbSession.atomic() as db:
                definition.is_enabled = False
                db.update(definition)
            assert client.post(path, json=form, headers=headers).status_code == 404
            with DbSession.atomic() as db:
                definition.is_enabled = True
                db.update(definition)
            form["app_revision"] = definition.edit_revision()
            with DbSession.atomic() as db:
                state[4].is_selected = False
                db.update(state[4])
            revoked = client.post(path, json=form, headers=headers)
            assert revoked.status_code == 200 and revoked.json()["items"] == []
            assert client.post(path, json={**form, "after": items[0]["signal_uid"]}, headers=headers).status_code == 400
            with DbSession.atomic() as db:
                state[4].is_selected = True
                db.update(state[4])
            assert len(client.post(path, json=form, headers=headers).json()["items"]) == 1
            # Real admin update and native renewed consent, with provider data still stored.
            with DbSession.atomic() as db:
                admin = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
                admin.is_admin = True
                db.update(admin)
                panel_connection = AppConnection(
                    app_key="github-panel", owner_id=state[1][1].id, state="connected",
                    credential_reference=state[2].credential_reference,
                )
                db.insert(panel_connection)
            admin_access, admin_refresh = AuthSecurity.authenticate(admin.id)
            changed = {**definition.declaration, "version": "0.1.2"}
            if trust_change == "publisher":
                changed["publisher"] = "Changed publisher"
            elif trust_change == "panel":
                changed["panel"] = {**changed["panel"], "url": "https://new-panel.example.invalid"}
            elif trust_change == "mcp_identity":
                changed["mcp"] = {"identity": "changed-mcp-server", "tools": []}
            elif trust_change == "resource_types":
                changed["resource_types"] = [*changed["resource_types"], "application"]
            else:
                changed["trust"] = {key: [] for key in ("authentication_origins", "mcp_origins", "data_origins")}
                changed["trust"][trust_change] = ["https://changed.example.invalid"]
            payload = {"declaration": changed, "expected_revision": form["app_revision"]}
            assert client.post("/settings/apps/registry", json=payload, headers=headers).status_code == 403
            client.cookies.set(Env.REFRESH_TOKEN_NAME, admin_refresh)
            admin_headers = {"Authorization": f"Bearer {admin_access}"}
            stale = client.post("/settings/apps/registry", json={**payload, "expected_revision": "0" * 64}, headers=admin_headers)
            assert stale.status_code == 409
            updated = client.post("/settings/apps/registry", json=payload, headers=admin_headers)
            assert updated.status_code == 200, updated.text
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            assert client.post(path, json=form, headers=headers).status_code == 404
            with DbSession.use(readonly=False) as db:
                binding_now = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == panel_binding.id)).first()
                connection_now = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == panel_connection.id)).first()
                if trust_change == "resource_types":
                    assert binding_now.state == "enabled" and binding_now.granted_capabilities == ["panels.render"]
                    assert connection_now.state == "connected"
                else:
                    assert binding_now.state == "needs_attention" and binding_now.granted_capabilities == []
                    assert connection_now.state == "revoked"
            renewed = client.put(
                f"/board/{state[1][2].get_uid()}/settings/apps/github-panel/consent",
                json={"app_revision": updated.json()["app"]["revision"], "binding_uid": binding_now.get_uid(),
                      "expected_revision": binding_now.edit_revision(), "capabilities": ["panels.render", "signals.read"]},
                headers=headers,
            )
            assert renewed.status_code == 200, renewed.text
            form.update(app_revision=updated.json()["app"]["revision"], binding_revision=renewed.json()["revision"])
            assert len(client.post(path, json=form, headers=headers).json()["items"]) == 1
            with DbSession.use(readonly=False) as db:
                expected_state = "connected" if trust_change == "resource_types" else "revoked"
                assert db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == panel_connection.id)).first().state == expected_state
            service.secret_reference.revoke(state[1][1], state[2].credential_reference, 0)
            assert client.post(path, json=form, headers=headers).json()["items"] == []
    finally:
        domain.close()


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_personal_inbox_multiboard_authority_paging_and_hidden_links(scoped, monkeypatch):
    from langboard.apps.SignalInbox import list_personal_signals
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.models import (
        AppResourceBinding,
        AppSignal,
        BoardAppBinding,
        Project,
        User,
    )

    state, card, link, _ = scoped
    service = state[0]
    service.card = DomainService().card
    actor = state[1][1]
    with DbSession.atomic() as db:
        link.is_enabled = False
        db.update(link)
    first = send(state)
    with DbSession.atomic() as db:
        source = db.exec(SqlBuilder.select.table(AppSignal)).first()
        board = Project(
            title="Second authorized source", owner_id=actor.id, organization_id=state[1][2].organization_id
        )
        db.insert(board)
        consent = BoardAppBinding(
            project_id=board.id, app_key="github", state="enabled", granted_capabilities=["signals.read"]
        )
        db.insert(consent)
        resource = AppResourceBinding(
            board_binding_id=consent.id,
            connection_id=state[2].id,
            resource_type="repository",
            external_resource_id="second",
            is_selected=True,
            access_state="granted",
        )
        db.insert(resource)
        second = AppSignal(
            resource_id=resource.id,
            provider="github",
            event_id="second",
            external_id="second",
            event_type=source.event_type,
            commit_sha=source.commit_sha,
            outcome=source.outcome,
            occurred_at=source.occurred_at,
            payload_digest="a" * 64,
        )
        db.insert(second)

    def read(after=None, **kwargs):
        return list_personal_signals(service, actor, after, channel=CollaborationChannel.HumanUI, **kwargs)

    assert {item["project_uid"] for item in read()["items"]} == {board.get_uid(), state[1][2].get_uid()}
    page = read(page_size=1)
    assert [item["signal_uid"] for item in page["items"]] == [second.get_uid()]
    assert page["next_cursor"] and page["next_cursor"] != second.get_uid()
    assert [item["signal_uid"] for item in read(page["next_cursor"], page_size=1)["items"]] == [first["signal_uid"]]
    for altered in (page["next_cursor"] + "tampered", page["next_cursor"] + "=",
                    page["next_cursor"] + "\n", " " + page["next_cursor"]):
        with pytest.raises(ValueError):
            read(altered)
    with DbSession.atomic() as db:
        stranger = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
    with pytest.raises(ValueError):
        list_personal_signals(service, stranger, page["next_cursor"])
    # Each source is independently scoped before pagination; hidden/deleted boards
    # and denied resources cannot occupy result slots or reveal board metadata.
    from langboard_shared.core.types import SafeDateTime

    with DbSession.atomic() as db:
        board.deleted_at = SafeDateTime.now()
        db.update(board)
    assert [item["signal_uid"] for item in read()["items"]] == [first["signal_uid"]]
    with DbSession.atomic() as db:
        board.deleted_at = None
        resource.access_state = "denied"
        db.update(board)
        db.update(resource)
    assert [item["signal_uid"] for item in read()["items"]] == [first["signal_uid"]]
    with DbSession.atomic() as db:
        resource.access_state = "granted"
        db.update(resource)
    # A visible link removes the signal; another person's private link reveals nothing.
    with DbSession.atomic() as db:
        link.is_enabled = True
        card.visibility = "PRIVATE"
        card.owner_user_id = 2
        card.created_by_user_id = 2
        db.update(link)
        db.update(card)
    assert len(read()["items"]) == 2
    assert all("card_uid" not in item for item in read()["items"])
    with DbSession.atomic() as db:
        card.owner_user_id = actor.id
        card.created_by_user_id = actor.id
        db.update(card)
    assert len(read()["items"]) == 1
    with DbSession.atomic() as db:
        consent.granted_capabilities = []
        db.update(consent)
    assert read()["items"] == []
    with pytest.raises(ValueError):
        read(page["next_cursor"])
    with DbSession.atomic() as db:
        consent.granted_capabilities = ["signals.read"]
        db.update(consent)
        state[2].state = "revoked"
        db.update(state[2])
    assert read()["items"] == []


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_personal_inbox_bounded_credential_denials_preserve_continuation(scoped):
    from langboard.apps.SignalInbox import list_personal_signals
    from langboard_shared.domain.models import AppConnection, AppResourceBinding, AppSignal

    state, _, link, _ = scoped
    state[0].card = DomainService().card
    with DbSession.atomic() as db:
        link.is_enabled = False
        db.update(link)
    visible = send(state)
    with DbSession.atomic() as db:
        source = db.exec(SqlBuilder.select.table(AppSignal)).first()
        denied_connection = AppConnection(
            app_key="github",
            owner_id=state[1][1].id,
            state="connected",
            ownership="organization",
            organization_id=state[1][2].organization_id,
            credential_reference="secret://ref/invalid",
        )
        db.insert(denied_connection)
        denied = AppResourceBinding(
            board_binding_id=state[3].id,
            connection_id=denied_connection.id,
            resource_type="repository",
            external_resource_id="denied",
            is_selected=True,
            access_state="granted",
        )
        db.insert(denied)
        for index in range(105):
            db.insert(
                AppSignal(
                    resource_id=denied.id,
                    provider="github",
                    event_id=f"denied-{index}",
                    external_id=f"denied-{index}",
                    event_type=source.event_type,
                    commit_sha=source.commit_sha,
                    outcome=source.outcome,
                    occurred_at=source.occurred_at,
                    payload_digest="a" * 64,
                )
            )
    first = list_personal_signals(state[0], state[1][1])
    assert first["items"] == [] and first["next_cursor"]
    final = list_personal_signals(state[0], state[1][1], first["next_cursor"])
    assert [item["signal_uid"] for item in final["items"]] == [visible["signal_uid"]]
    assert final["next_cursor"] is None


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_personal_inbox_authenticated_http_and_current_board_revocation(scoped, monkeypatch):
    import importlib
    import langboard.routes.board.SignalInboxApi as inbox_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    state, _, link, _ = scoped
    service = state[0]
    service.card = DomainService().card
    service.close = lambda: None
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    with DbSession.atomic() as db:
        link.is_enabled = False
        db.update(link)
    send(state)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    for route in app.routes:
        if getattr(route, "endpoint", None) in (inbox_api.get_personal_signal_inbox, inbox_api.get_signal_inbox):
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/dashboard/signals/inbox").status_code == 401
        access, refresh = AuthSecurity.authenticate(state[1][1].id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        response = client.get("/dashboard/signals/inbox", headers=headers)
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["project_uid"] == state[1][2].get_uid()
        source = f"/board/{item['project_uid']}/signals/inbox"
        selected = client.get(source, params={"signal_uid": item["signal_uid"]}, headers=headers)
        assert selected.status_code == 200 and selected.json()["items"][0]["signal_uid"] == item["signal_uid"]
        assert client.get(source, params={"signal_uid": "../invalid"}, headers=headers).status_code == 400
        with DbSession.atomic() as db:
            state[1][4].actions = []
            db.update(state[1][4])
        assert client.get("/dashboard/signals/inbox", headers=headers).json() == {"items": [], "next_cursor": None}
        assert client.get(source, params={"signal_uid": item["signal_uid"]}, headers=headers).status_code == 404
