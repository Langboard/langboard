# ruff: noqa: F811
"""Independent provider declaration/normalizer snapshots through native HTTP and SQL."""

import hashlib
import hmac
import json
import time
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
from test_github_signal import board, installation, lifecycle, secrets, signal_storage  # noqa: F401


CONTRACTS = json.loads((Path(__file__).parent / "vendor/provider-signal-contracts.json").read_text())


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda value: value["definition"]["key"])
def test_provider_contract_signed_http_sql_replay_and_revocation(scoped, monkeypatch, contract):
    import langboard.routes.settings.AppSignalApi as signal_api
    from langboard.apps.SignalInbox import list_board_signals
    from langboard_shared.domain.services import DomainService

    state, _, _, _ = scoped
    service, actor, project = state[0], state[1][1], state[1][2]
    with DbSession.use(readonly=False) as db:
        cards_before = {row.id for row in db.exec(SqlBuilder.select.table(Card)).all()}
        links_before = {row.id for row in db.exec(SqlBuilder.select.table(CardAppSignalBinding)).all()}
    definition = validate_app_definition(contract["definition"])
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
        for field in ("event_id", "event_type", "external_id", "outcome", "commit_sha"):
            assert getattr(stored, field) == envelope[field]
    visible = list_board_signals(service, actor, project.get_uid(), provider=definition["key"])
    assert len(visible["items"]) == 1
    assert visible["items"][0]["outcome"] == envelope["outcome"]
    assert visible["items"][0]["time_basis"] == definition["signal_policy"]["time_basis"]
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
