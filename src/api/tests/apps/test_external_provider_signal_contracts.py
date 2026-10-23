# ruff: noqa: F811
"""Independent provider declaration/normalizer snapshots through native HTTP and SQL."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard_sdk.definition import validate_app_definition
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppResourceBinding,
    AppSignal,
    BoardAppBinding,
    Card,
    CardAppSignalBinding,
)
from langboard_shared.domain.services.AppConnectionAuthentication import (
    issue_connection_credential,
    revoke_connection_credential,
)
from langboard_shared.publishers import CardPublisher
from test_card_signal_projection import scoped  # noqa: F401
from test_github_signal import installation, lifecycle, signal_storage  # noqa: F401


CONTRACTS = json.loads((Path(__file__).parent / "vendor/provider-signal-contracts.json").read_text())


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda value: f'{value["definition"]["key"]}-{value["evidence"]["event_type"]}')
def test_provider_contract_signed_http_sql_replay_and_revocation(scoped, monkeypatch, contract):
    import langboard.routes.settings.AppSignalApi as signal_api
    from langboard.apps.SignalInbox import list_board_signals
    from langboard_shared.domain.services import DomainService

    state, card, link, _ = scoped
    service, actor, project = state[0], state[1][1], state[1][2]
    with DbSession.use(readonly=False) as db:
        cards_before = {row.id for row in db.exec(SqlBuilder.select.table(Card)).all()}
        links_before = {row.id for row in db.exec(SqlBuilder.select.table(CardAppSignalBinding)).all()}
    definition = validate_app_definition(contract["definition"])
    basis_policy = definition["signal_policy"]["time_basis"]
    expected_basis = (
        basis_policy[contract["evidence"]["event_type"]] if isinstance(basis_policy, dict) else basis_policy
    )
    expected_kind = "external_observation" if expected_basis == "observation" else "external_event"
    expected_time = datetime.fromisoformat(contract["evidence"]["occurred_at"].replace("Z", "+00:00")).astimezone(
        timezone.utc
    ).isoformat(timespec="microseconds")
    AppConnectionCredential.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.atomic() as db:
        registered = AppDefinition(key=definition["key"], declaration=definition, approved_by=actor.id)
        db.insert(registered)
        connection = AppConnection(
            app_key=definition["key"],
            owner_id=actor.id,
            ownership="organization",
            organization_id=project.organization_id,
            state="connected",
        )
        db.insert(connection)
        binding = BoardAppBinding(
            project_id=project.id,
            app_key=definition["key"],
            state="enabled",
            granted_capabilities=["panels.render", "signals.read", "signals.publish"],
        )
        db.insert(binding)
        resource = AppResourceBinding(
            board_binding_id=binding.id,
            connection_id=connection.id,
            resource_type=definition["signal_policy"]["resource_types"][0],
            external_resource_id="fixture",
            access_state="granted",
            is_selected=True,
        )
        db.insert(resource)
    issued = issue_connection_credential(actor, connection.id)
    token = issued["token"]
    envelope = {
        "version": 1,
        "board_uid": project.get_uid(),
        "resource_uid": resource.get_uid(),
        "app_revision": registered.edit_revision(),
        "binding_revision": binding.edit_revision(),
        **contract["evidence"],
    }
    service.close = lambda: None
    service.card = DomainService().card
    monkeypatch.setattr(signal_api, "DomainService", lambda: service)
    monkeypatch.setattr(CardPublisher, "app_signal_changed", lambda _: None)
    app = FastAPI()
    app.include_router(AppRouter.api)
    client = TestClient(app)

    def publish(value=envelope):
        body = json.dumps(value, separators=(",", ":")).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(token.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return client.post(
            f"/apps/v1/boards/{project.get_uid()}/signals",
            content=body,
            headers={
                "Authorization": "Bearer " + token,
                "x-langboard-webhook-version": "1",
                "x-langboard-webhook-timestamp": stamp,
                "x-langboard-webhook-signature": "v1=" + signature,
            },
        )

    first = publish()
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    replay = publish()
    assert replay.json() == {**first.json(), "duplicate": True}
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(AppSignal).where(AppSignal.resource_id == resource.id)).all()
        assert len(rows) == 1
        stored = rows[0]
        assert stored.provider == definition["key"]
        assert stored.occurred_at == expected_time
        for field in ("event_id", "event_type", "external_id", "outcome", "commit_sha"):
            assert getattr(stored, field) == envelope[field]
    visible = list_board_signals(service, actor, project.get_uid(), provider=definition["key"])
    assert len(visible["items"]) == 1
    selected = visible["items"][0]
    assert selected["signal_uid"] == first.json()["signal_uid"]
    assert selected["time_basis"] == expected_basis and selected["evidence_kind"] == expected_kind
    assert selected["occurred_at"] == expected_time and selected["resource_uid"] == resource.get_uid()
    for field in ("event_type", "external_id", "outcome", "commit_sha"):
        assert selected[field] == envelope[field]
    # Explicitly bind this emitted source event to a card, then read its exact proof.
    # Restore the existing link before checking publication cannot mutate card authority.
    from langboard_shared.domain.services.AppSignalProjection import card_signal_projections

    original_link = (link.resource_id, link.external_id, link.commit_sha, link.is_enabled)
    with DbSession.atomic() as db:
        link.resource_id, link.external_id, link.commit_sha, link.is_enabled = (
            resource.id, envelope["external_id"], envelope["commit_sha"], True
        )
        db.update(link)
    proof = card_signal_projections([card])[card.id][0]
    for field in ("signal_uid", "event_type", "external_id", "outcome", "commit_sha",
                  "occurred_at", "resource_uid", "time_basis", "evidence_kind"):
        assert proof[field] == selected[field]
    assert proof["state"] == definition["signal_policy"]["outcome_states"][envelope["outcome"]]
    with DbSession.atomic() as db:
        link.resource_id, link.external_id, link.commit_sha, link.is_enabled = original_link
        db.update(link)
    from langboard_shared.domain.services.AppPanel import get_panel, set_panel

    closed = set_panel(service.workflow_stage, actor, project.get_uid(), definition["key"],
        binding.get_uid(), binding.edit_revision(), registered.edit_revision(), False)
    assert closed["granted_capabilities"] == ["signals.publish", "signals.read"]
    assert get_panel(service.workflow_stage, actor, project.get_uid(), definition["key"]) is None
    # The publication revision changes with the grant; the caller must refresh
    # it even though signal consent itself remains valid.
    assert publish().status_code == 409
    envelope["binding_revision"] = closed["revision"]
    # A persisted event envelope is immutable, including its authority revision.
    assert publish().status_code == 409
    envelope["event_id"] += "-after-panel-close"
    after_close = publish()
    assert after_close.status_code == 200, after_close.text
    assert after_close.json()["duplicate"] is False
    assert publish().json() == {**after_close.json(), "duplicate": True}
    # Inbox projects one current observation per external identity, while SQL
    # retains both event receipts.
    assert len(list_board_signals(service, actor, project.get_uid(), provider=definition["key"])["items"]) == 1
    # Publication, replay and inbox reads must not create cards or link evidence.
    # A separately authorized card command owns that transition.
    with DbSession.use(readonly=False) as db:
        assert {row.id for row in db.exec(SqlBuilder.select.table(Card)).all()} == cards_before
        assert {row.id for row in db.exec(SqlBuilder.select.table(CardAppSignalBinding)).all()} == links_before
    assert publish({**envelope, "outcome": "undeclared"}).status_code == 400
    revoke_connection_credential(actor, connection.id, SnowflakeID.from_short_code(issued["credential_uid"]))
    assert publish().status_code == 403
    # A fresh credential isolates board disablement from credential revocation.
    token = issue_connection_credential(actor, connection.id)["token"]
    assert publish().json() == {**after_close.json(), "duplicate": True}
    disabled = service.workflow_stage.disable_app_binding(actor, project.get_uid(), definition["key"],
        binding.get_uid(), closed["revision"])
    assert disabled.state == "disabled" and disabled.granted_capabilities == []
    assert publish().status_code == 403
    assert list_board_signals(service, actor, project.get_uid(), provider=definition["key"])["items"] == []
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(AppSignal).where(AppSignal.resource_id == resource.id)).all()) == 2
        assert {row.id for row in db.exec(SqlBuilder.select.table(Card)).all()} == cards_before
        assert {row.id for row in db.exec(SqlBuilder.select.table(CardAppSignalBinding)).all()} == links_before
        assert db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection.id)).first().state == "connected"


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("policy_change", ["entry", "scalar"])
def test_mixed_event_basis_selected_proof_ties_and_policy_consent(scoped, monkeypatch, policy_change):
    from copy import deepcopy
    import langboard.routes.settings.AppSignalApi as signal_api
    from langboard.apps.SignalInbox import list_board_signals
    from langboard_shared.domain.models import User
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppRegistry import (
        AppRegistryConflict,
        approved_manifests,
        save_definition,
        set_board_consent,
    )
    from langboard_shared.domain.services.AppSignalProjection import card_signal_projections
    from langboard_shared.domain.services.CardWorkState import project_work_state
    from langboard_shared.publishers import AppSettingPublisher

    state, card, link, _ = scoped
    service, actor, project = state[0], state[1][1], state[1][2]
    declaration = validate_app_definition({
        "schema_version": 2, "key": "mixed-events", "name": "Mixed Events", "version": "1.0.0",
        "description": "Independent mixed event fixture", "resource_types": ["service"],
        "capabilities": ["signals.read", "signals.publish", "resources.read"],
        "signal_sources": ["mixed-events"],
        "trust": {"authentication_origins": [], "mcp_origins": [], "data_origins": []},
        "signal_policy": {
            "version": 1, "event_types": ["service.observed", "service.changed"],
            "resource_types": ["service"], "commit_mode": "empty",
            "time_basis": {"service.observed": "observation", "service.changed": "provider_occurrence"},
            "outcome_states": {"success": "passed", "failure": "failed"},
        },
    })
    AppConnectionCredential.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    monkeypatch.setattr(CardPublisher, "app_signal_changed", lambda _: None)
    with DbSession.atomic() as db:
        admin = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)).first()
        admin.is_admin = True
        db.update(admin)
    saved = save_definition(actor, declaration)
    with DbSession.atomic() as db:
        connection = AppConnection(app_key=declaration["key"], owner_id=actor.id,
            ownership="organization", organization_id=project.organization_id, state="connected")
        db.insert(connection)
        binding = BoardAppBinding(project_id=project.id, app_key=declaration["key"], state="enabled",
            granted_capabilities=declaration["capabilities"])
        db.insert(binding)
        resource = AppResourceBinding(board_binding_id=binding.id, connection_id=connection.id,
            resource_type="service", external_resource_id="mixed-service", access_state="granted", is_selected=True)
        db.insert(resource)
        link.resource_id = resource.id
        link.commit_sha = ""
        # Disabled while reading Inbox, enabled when reading card evidence.
        link.is_enabled = False
        db.update(link)
    token = issue_connection_credential(actor, connection.id)["token"]
    service.close = lambda: None
    service.card = DomainService().card
    monkeypatch.setattr(signal_api, "DomainService", lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    client = TestClient(app)
    envelope = {"version": 1, "board_uid": project.get_uid(), "resource_uid": resource.get_uid(),
        "app_revision": saved["revision"], "binding_revision": binding.edit_revision(),
        "event_id": "observed-first", "event_type": "service.observed", "external_id": "55",
        "commit_sha": "", "occurred_at": "2026-10-08T01:00:00Z", "outcome": "success"}

    def publish(value):
        body = json.dumps(value, separators=(",", ":")).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(token.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return client.post(f"/apps/v1/boards/{project.get_uid()}/signals", content=body, headers={
            "Authorization": "Bearer " + token, "x-langboard-webhook-version": "1",
            "x-langboard-webhook-timestamp": stamp, "x-langboard-webhook-signature": "v1=" + signature})

    def projection():
        with DbSession.atomic() as db:
            link.is_enabled = True
            db.update(link)
        proof = card_signal_projections([card])[card.id][0]
        with DbSession.atomic() as db:
            link.is_enabled = False
            db.update(link)
        return proof

    def inbox():
        return list_board_signals(service, actor, project.get_uid(), provider=declaration["key"])["items"]

    policy = approved_manifests()[declaration["key"]].signal_policy
    assert policy.time_basis is None and isinstance(policy.event_time_bases, tuple)
    declaration["signal_policy"]["time_basis"]["service.observed"] = "provider_occurrence"
    assert policy.basis_for_event("service.observed") == "observation"
    declaration["signal_policy"]["time_basis"]["service.observed"] = "observation"
    empty = projection()
    assert empty["state"] == "unavailable" and empty["signal_uid"] is None
    assert empty["time_basis"] is None and empty["evidence_kind"] is None
    assert inbox() == []
    first = publish(envelope)
    assert first.status_code == 200, first.text
    assert publish(envelope).json() == {**first.json(), "duplicate": True}
    assert publish({**envelope, "time_basis": "provider_occurrence"}).status_code == 400
    assert publish({**envelope, "outcome": "failure"}).status_code == 409
    assert publish({**envelope, "event_id": "undeclared", "event_type": "service.unapproved"}).status_code == 400
    assert publish({**envelope, "event_id": "bad-commit", "commit_sha": "a" * 40}).status_code == 400
    assert publish({**envelope, "event_id": "bad-outcome", "outcome": "unapproved"}).status_code == 400

    def assert_semantics(basis, kind, event_type, receipt, state="passed"):
        row, proof = inbox()[0], projection()
        for item in (row, proof):
            assert item["time_basis"] == basis and item["evidence_kind"] == kind
            assert item["event_type"] == event_type and item["signal_uid"] == receipt["signal_uid"]
        assert proof["state"] == state
        return proof

    assert_semantics("observation", "external_observation", "service.observed", first.json())
    changed = {**envelope, "event_id": "changed", "event_type": "service.changed",
               "occurred_at": "2026-10-08T02:00:00Z"}
    second = publish(changed)
    assert second.status_code == 200, second.text
    assert_semantics("provider_occurrence", "external_event", "service.changed", second.json())
    # A later arrival with an older occurrence cannot replace the latest proof.
    assert publish({**envelope, "event_id": "old-arrival"}).status_code == 200
    assert_semantics("provider_occurrence", "external_event", "service.changed", second.json())
    tied = {**changed, "event_id": "observed-tie", "event_type": "service.observed"}
    third = publish(tied)
    assert third.status_code == 200, third.text
    assert_semantics(None, None, "service.observed", third.json())
    conflict = publish({**tied, "event_id": "conflicting-tie", "outcome": "failure"})
    assert conflict.status_code == 200, conflict.text
    ambiguous = assert_semantics(None, None, "service.observed", conflict.json(), "conflict")
    assert inbox()[0]["conflict"] and ambiguous["outcome"] is None
    work = project_work_state(card_uid=card.get_uid(), workflow_stage="active", archived=False,
        linked_resource=False, total=1, completed=0, started=0, paused=0, external_signals=[ambiguous],
        workflow_policy={"key": "active", "counts_as_completed": False,
                         "active_queue_policy": "include", "overdue_policy": "normal"})
    assert any(reason["code"] == "external_signal_conflict" for reason in work["reasons"])
    assert work["external_execution_state"] is None and work["verification"] is None
    assert not work["completed"] and work["pending_approval_count"] == 0
    latest = {**envelope, "event_id": "observed-latest", "occurred_at": "2026-10-08T03:00:00Z"}
    fourth = publish(latest)
    assert fourth.status_code == 200, fourth.text
    assert_semantics("observation", "external_observation", "service.observed", fourth.json())
    with DbSession.atomic() as db:
        card.last_change_seq = 8
        db.update(card)
    assert projection()["state"] == "stale"
    assert projection()["time_basis"] == "observation"
    with DbSession.atomic() as db:
        card.last_change_seq = 7
        resource.is_selected = False
        db.update(card)
        db.update(resource)
        link.is_enabled = True
        db.update(link)
    assert card_signal_projections([card]) == {} and inbox() == []
    assert publish({**latest, "event_id": "revoked-resource"}).status_code == 403
    with DbSession.atomic() as db:
        resource.is_selected = True
        link.is_enabled = False
        db.update(resource)
        db.update(link)
    # Map order is immaterial. Real host updates still advance app/binding revisions.
    reordered = deepcopy(declaration)
    reordered["version"] = "1.0.1"
    reordered["signal_policy"]["time_basis"] = dict(reversed(list(reordered["signal_policy"]["time_basis"].items())))
    reordered_saved = save_definition(actor, reordered, saved["revision"])
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
    assert set(binding.granted_capabilities) == set(declaration["capabilities"])
    assert publish({**latest, "event_id": "stale-app"}).status_code == 409
    revised = {**latest, "event_id": "new-revisions", "app_revision": reordered_saved["revision"],
               "binding_revision": binding.edit_revision()}
    assert publish(revised).status_code == 200
    assert publish({**envelope, "app_revision": revised["app_revision"],
                    "binding_revision": revised["binding_revision"]}).status_code == 409
    updated = deepcopy(reordered)
    updated["version"] = "1.0.2"
    if policy_change == "entry":
        updated["signal_policy"]["time_basis"]["service.changed"] = "observation"
    else:
        updated["signal_policy"]["time_basis"] = "observation"
    updated_saved = save_definition(actor, updated, reordered_saved["revision"])
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
    assert binding.granted_capabilities == ["resources.read"]
    assert inbox() == []
    denied = {**revised, "event_id": "without-renewed-consent", "app_revision": updated_saved["revision"],
              "binding_revision": binding.edit_revision()}
    assert publish(denied).status_code == 403
    with pytest.raises(AppRegistryConflict):
        set_board_consent(service.workflow_stage, actor, project.get_uid(), declaration["key"],
            reordered_saved["revision"], binding.get_uid(), binding.edit_revision(), declaration["capabilities"])
    consent = set_board_consent(service.workflow_stage, actor, project.get_uid(), declaration["key"],
        updated_saved["revision"], binding.get_uid(), binding.edit_revision(), declaration["capabilities"])
    renewed_token = issue_connection_credential(actor, connection.id)["token"]
    token = renewed_token
    renewed = {**denied, "event_id": "renewed-consent", "binding_revision": consent["revision"]}
    response = publish(renewed)
    assert response.status_code == 200, response.text
    assert publish(renewed).json() == {**response.json(), "duplicate": True}
    assert_semantics("observation", "external_observation", "service.observed", response.json())
    assert card.project_column_id == state[1][5][0].id and card.last_change_seq == 7
    assert not binding.stage_transitions_enabled and binding.workflow_mapping == {}
    # Empty approved registries still produce valid SQL and an empty Inbox.
    with DbSession.atomic() as db:
        definitions = db.exec(SqlBuilder.select.table(AppDefinition)).all()
        for definition in definitions:
            definition.is_enabled = False
            db.update(definition)
        for key in {"github", "glitchtip", "dokploy"} - {item.key for item in definitions}:
            db.insert(AppDefinition(key=key, declaration={}, approved_by=actor.id, is_enabled=False))
    assert approved_manifests() == {}
    assert list_board_signals(service, actor, project.get_uid())["items"] == []
    client.close()
