# ruff: noqa: F811
"""A new human resume generation can reach signed reception and a new permit."""

import hashlib
import hmac
import importlib
import json
from secrets import token_hex
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    AppExecutionLease,
    AppExecutionOutbox,
    BoardAppBinding,
    EmployeeMembershipPolicy,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, finish_app_event
from langboard_shared.domain.services.AppExecutionAcknowledgments import acknowledge_app_execution
from langboard_shared.domain.services.AppExecutionControl import (
    request_human_runtime_resume,
    request_human_runtime_stop,
)
from langboard_shared.domain.services.AppExecutionLeases import authorize_app_runtime, check_app_runtime
from langboard_shared.domain.services.AppExecutionStarts import read_app_runtime_report, report_app_execution_start
from sqlalchemy import text
from test_app_execution_start import start_scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_resume_delivery_reception_and_new_runtime_preserve_stopped_generation(board, monkeypatch):
    _, card, token, old_request, old_lease, old_runtime = start_scope(board, monkeypatch)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    with DbSession.atomic() as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        binding.granted_capabilities = [*binding.granted_capabilities, "events.receive"]
        db.update(binding)
    service, actor, project_id = DomainService(), board[1], board[2].id
    stopped = request_human_runtime_stop(service, actor, project_id, card.id, old_request.id, old_request.generation)
    assert stopped["state"] == "stop_requested"
    assert check_app_runtime(old_lease, old_runtime, stopped=True)["state"] == "stopped"
    with DbEngine.get_main_engine().begin() as db:
        db.execute(text("UPDATE card_execution_generation SET execution_generation=:generation WHERE card_id=:card_id"),
                   {"generation": old_request.generation + 1, "card_id": int(card.id)})
    # Producer advancement is controlled fixture input, not a deployed producer.
    enqueues = []
    tasks = importlib.import_module("langboard_shared.tasks.webhooks.AppEventDeliveryTask")
    monkeypatch.setattr(tasks, "app_event_delivery_task", lambda *args: enqueues.append(args))
    resumed = request_human_runtime_resume(service, actor, project_id, card.id, old_request.id, old_request.generation)
    assert resumed["state"] == "resume_requested" and resumed["started"] is False
    request_id = SnowflakeID.from_short_code(resumed["request_uid"])
    with DbSession.use(readonly=False) as db:
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.request_id == request_id)).first()
        old_event = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.request_id == old_request.id)).first()
    assert enqueues == [(event.get_uid(), 1)]
    assert claim_app_event(old_event.id, 1) is None
    claim = claim_app_event(event.id, 1)
    assert claim is not None
    body = json.loads(claim["body"])
    assert body["request_uid"] == resumed["request_uid"] and body["generation"] == old_request.generation + 1
    assert body["started"] is False
    timestamp = claim["headers"]["X-Langboard-Webhook-Timestamp"]
    digest = hmac.new(b"secret", timestamp.encode() + b"." + claim["body"], hashlib.sha256).hexdigest()
    assert claim["headers"]["X-Langboard-Webhook-Signature"] == f"v1={digest}"
    ack = acknowledge_app_execution(token, project_id, card.id, request_id, event.id, "new-runtime")
    assert ack["state"] == "received" and ack["started"] is False
    assert finish_app_event(event.id, claim["claim_token"], delivered=True)["state"] == "delivered"
    runtime = token_hex(32)
    permit = authorize_app_runtime(token, project_id, card.id, request_id,
                                   SnowflakeID.from_short_code(ack["acknowledgment_uid"]), runtime)
    new_lease = SnowflakeID.from_short_code(permit["lease_uid"])
    assert new_lease != old_lease and permit["permit_execution"] is True
    assert read_app_runtime_report(new_lease, runtime)["active_report"] is False
    start = report_app_execution_start(token, project_id, card.id, request_id, new_lease, runtime, "new-process")
    assert start["evidence_kind"] == "app_attestation"
    assert read_app_runtime_report(new_lease, runtime)["active_report"] is True
    assert check_app_runtime(old_lease, old_runtime)["state"] == "stopped"
    replay = request_human_runtime_resume(service, actor, project_id, card.id, old_request.id, old_request.generation)
    assert replay["changed"] is False and replay["request_uid"] == resumed["request_uid"]
    assert len(enqueues) == 1
    with DbSession.use(readonly=False) as db:
        retained = db.exec(SqlBuilder.select.table(AppExecutionLease).where(AppExecutionLease.id == old_lease)).first()
        assert retained.state == "stopped"
        assert len([entry for entry in retained.history if entry.get("action") == "resume_requested"]) == 1
