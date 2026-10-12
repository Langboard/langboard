"""Optional installed third-party Monitor wrapper against native HTTP/PostgreSQL."""

import asyncio
import json
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from langboard_sdk.rest import HttpTransport, NativeApiError
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppConnection,
    AppDefinition,
    AppEventDestination,
    AppExecutionAcknowledgment,
    AppExecutionOutbox,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppOwnershipAudit,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
    ProjectColumn,
    ProjectRole,
    WorkflowStageDefinition,
    WebhookSetting,
)
from langboard_shared.domain.services.AppCardInteractions import respond_to_app_card_interaction
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.domain.services.CardAppGovernance import set_card_app_ownership
from langboard_shared.domain.services.CardAppResources import read_card_app_resources, set_card_app_resources
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_app_connection_auth_postgres import connected, registered  # noqa: F401
from test_app_governance_postgres import governance  # noqa: F401


def test_installed_monitor_question_recovery_response_ack_and_revocation(registered, monkeypatch):  # noqa: F811
    monitor = pytest.importorskip("example_monitor")
    answers = pytest.importorskip("example_monitor.answers")
    from langboard_shared.core.broker import Broker

    # Suppress queue dispatch only; all native HTTP, ACL and database operations are real.
    monkeypatch.setattr(Broker.celery, "send_task", lambda *args, **kwargs: None)
    engine, (admin, owner, _), connection, _ = registered
    for model in (
        WorkflowStageDefinition,
        ProjectColumn,
        Card,
        CardAppOwnership,
        CardAppOwnershipAudit,
        AppResourceBinding,
        CardAppResourceSelection,
        CardAppResourceSelectionAudit,
        AppCardInteraction,
        AppCardInteractionResponse,
        AppExecutionOutbox,
        AppExecutionAcknowledgment,
        ProjectRole,
        AppEventDestination,
        WebhookSetting,
    ):
        model.__table__.create(engine)
    assert monitor.DECLARATION["capabilities"] == ["signals.read", "signals.publish"]
    declaration = deepcopy(monitor.DECLARATION)
    # Explicit acceptance-fixture consent; no widening of the deployed baseline app.
    declaration["capabilities"] += ["cards.create", "resources.read", "interactions.request", "events.receive"]
    declaration["trust"] = {"authentication_origins": [], "mcp_origins": [], "data_origins": ["https://example.test"]}
    declaration["card_lifecycle"] = {
        "issue": {"archive": {"mode": "retain", "description": "Retain"}, "restore": "retain_external_state"}
    }
    with Session(engine) as db:
        db.execute(update(AppDefinition).values(key="example-monitor", declaration=declaration))
        db.execute(update(AppConnection).where(AppConnection.id == connection.id).values(app_key="example-monitor"))
        db.execute(
            update(BoardAppBinding).values(app_key="example-monitor", granted_capabilities=declaration["capabilities"])
        )
        db.add(WorkflowStageDefinition(id=201, key="ready", name="Ready", is_builtin=True))
        db.flush()
        db.add(ProjectColumn(id=211, project_id=20, name="Queue", workflow_stage="ready"))
        db.flush()
        db.add(Card(id=221, project_id=20, project_column_id=211, title="Independent question"))
        db.commit()
        app_revision = db.query(AppDefinition).one().edit_revision()
        binding_revision = db.query(BoardAppBinding).one().edit_revision()
    ids = (SnowflakeID(20), SnowflakeID(221))
    set_card_app_ownership(owner, *ids, "example-monitor", None, app_revision)
    initial = read_card_app_resources(owner, *ids, "example-monitor", connection.id)
    with Session(engine) as db:
        db.add(
            AppResourceBinding(
                id=301,
                board_binding_id=80,
                connection_id=connection.id,
                resource_type="issue",
                external_resource_id="one",
                is_selected=True,
                access_state="granted",
                identity_hash=initial["identity_revision"],
            )
        )
        db.commit()
    set_card_app_resources(
        owner,
        *ids,
        "example-monitor",
        connection.id,
        [SnowflakeID(301).to_short_code()],
        None,
        1,
        initial["identity_revision"],
        app_revision,
        binding_revision,
    )
    token = issue_connection_credential(owner, connection.id)["token"]
    from langboard_shared.domain.services.AppEventDestination import bind_app_event_destination

    with Session(engine) as db:
        db.add(
            WebhookSetting(
                id=801,
                name="Monitor receiver",
                url="https://example.test/events",
                secret_id="monitor-test-secret",
                events=["io.langboard.app.interaction.answered.v1"],
            )
        )
        db.commit()
    bind_app_event_destination(admin, connection.id, SnowflakeID(801), None)
    app = FastAPI()
    app.include_router(AppRouter.api)
    question = {
        "schema_version": 1,
        "kind": "question",
        "app_request_key": "monitor_native_question",
        "prompt": {"en": "Inspect or defer?", "ko": "점검할까요?"},
        "questions": [
            {
                "id": "action",
                "type": "single_choice",
                "prompt": {"en": "Action"},
                "choices": [{"id": "inspect", "label": {"en": "Inspect"}}, {"id": "defer", "label": {"en": "Defer"}}],
            }
        ],
        "presentation": {"type": "choice_card"},
        "expires_at": (SafeDateTime.now() + timedelta(hours=1)).isoformat(),
    }

    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver", headers={"Authorization": f"Bearer {token}"}
        ) as client:
            transport = HttpTransport(client)
            board_uid, card_uid = (value.to_short_code() for value in ids)
            missing = await monitor.Monitor.recover_question(transport, board_uid, card_uid, question)
            assert missing["state"] == "missing"
            created = await monitor.Monitor.question(transport, board_uid, card_uid, question)
            recovered = await monitor.Monitor.recover_question(transport, board_uid, card_uid, question)
            assert recovered["interaction_uid"] == created["interaction_uid"]
            assert recovered["request"] == question and recovered["state"] == "pending"
            with pytest.raises(ValueError):
                await monitor.Monitor.recover_question(
                    transport, board_uid, card_uid, {**question, "prompt": {"en": "Changed"}}
                )
            answer = respond_to_app_card_interaction(
                owner,
                *ids,
                SnowflakeID.from_short_code(created["interaction_uid"]),
                {"action": {"choice_id": "inspect"}},
                1,
                "monitor-human-answer-001",
            )
            read = await monitor.Monitor.current_response(transport, board_uid, card_uid, created["interaction_uid"])
            assert read["state"] == "answered" and read["response"] == answer["response"]
            with DbSession.atomic() as db:
                event = db.exec(
                    SqlBuilder.select.table(AppExecutionOutbox).where(
                        AppExecutionOutbox.request_id == SnowflakeID.from_short_code(answer["response"]["response_uid"])
                    )
                ).first()
                event_id = event.id
                event_uid = event.get_uid()
            from langboard_shared.core.security import KeyVault
            from langboard_shared.tasks.webhooks import AppEventDeliveryTask
            from langboard_shared.tasks.webhooks.utils import WebhookUrlPolicy

            receiver = FastAPI()
            received = []

            @receiver.post("/events")
            async def receive(request: Request):
                acknowledgment = await answers.receive_answer(
                    transport, await request.body(), request.headers, "monitor-acceptance-signing-secret"
                )
                received.append(acknowledgment)
                return acknowledgment

            with monkeypatch.context() as delivery:
                # Replace only fixture DNS, secret lookup and network transport.
                # Native current authority, claim, signing, receiver and receipts run.
                delivery.setattr(WebhookUrlPolicy, "getaddrinfo", lambda *args: [(2, 1, 6, "", ("93.184.216.34", 443))])
                delivery.setattr(
                    KeyVault,
                    "get_key",
                    lambda reference: (
                        "monitor-acceptance-signing-secret" if reference == "monitor-test-secret" else None
                    ),
                )
                delivery.setattr(
                    AppEventDeliveryTask,
                    "AsyncClient",
                    lambda **kwargs: AsyncClient(transport=ASGITransport(app=receiver), **kwargs),
                )
                await AppEventDeliveryTask.deliver_app_event(event_id)
                await AppEventDeliveryTask.deliver_app_event(event_id)
            assert len(received) == 1
            acknowledgment = received[0]
            with Session(engine) as db:
                delivered = db.get(AppExecutionOutbox, event_id)
                assert delivered.state == "delivered" and delivered.attempt == 1
                assert delivered.claim_token is None
                assert [item["kind"] for item in delivered.delivery_receipts] == ["claimed", "destination", "outcome"]
                assert delivered.delivery_receipts[-1]["outcome"] == "delivered"
                assert "monitor-acceptance-signing-secret" not in json.dumps(delivered.delivery_receipts)
                ack = db.query(AppExecutionAcknowledgment).one()
                assert ack.event_id == event_id and ack.runtime_reference == "example-monitor:received:" + event_uid
            assert acknowledgment["state"] == "received"
            assert acknowledgment["execution_resumed"] is False and acknowledgment["workflow_transitioned"] is False
            assert not read["execution_resumed"] and not read["workflow_transitioned"]
            with Session(engine) as db:
                assert db.query(AppCardInteraction).count() == 1
                assert db.query(AppCardInteractionResponse).count() == 1
                assert db.get(Card, 221).project_column_id == 211
                db.execute(update(AppResourceBinding).values(access_state="revoked"))
                db.commit()
            for operation in (
                monitor.Monitor.current_response(transport, board_uid, card_uid, created["interaction_uid"]),
                monitor.Monitor.recover_question(transport, board_uid, card_uid, question),
            ):
                with pytest.raises(NativeApiError) as denied:
                    await operation
                assert denied.value.status_code == 403

    asyncio.run(run())
