"""Native SDK request and human response boundary on disposable PostgreSQL."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import langboard.routes.settings.AppCardInteractionApi  # noqa: F401
import langboard.routes.settings.AppEventDestinationApi  # noqa: F401
import langboard.routes.settings.CardHumanInteractionApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_sdk.interactions import AppCardInteractions
from langboard_sdk.rest import HttpTransport
from langboard_sdk.webhooks import verify_webhook
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity, KeyVault
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppDefinition,
    AppEventDestination,
    AppExecutionAcknowledgment,
    AppExecutionOutbox,
    AppResourceBinding,
    BoardAppBinding,
    Bot,
    Card,
    CardAppOwnership,
    CardAppOwnershipAudit,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
    CardComment,
    CardCommentReaction,
    ProjectColumn,
    ProjectRole,
    WebhookSetting,
    WorkflowStageDefinition,
)
from langboard_shared.domain.services.AppCardInteractions import (
    accept_current_comment_reaction,
    cancel_app_card_interaction,
    create_app_card_interaction,
    read_app_card_interaction,
    respond_to_app_card_interaction,
)
from langboard_shared.domain.services.AppConnectionAuthentication import (
    issue_connection_credential,
    revoke_connection_credential,
)
from langboard_shared.domain.services.AppEventDestination import (
    bind_app_event_destination,
    manage_app_event_destination,
    prepare_app_event_signature,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.CardAppGovernance import set_card_app_ownership
from langboard_shared.domain.services.CardAppResources import read_card_app_resources, set_card_app_resources
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.Env import Env
from langboard_shared.publishers import CardCommentPublisher
from langboard_shared.security import Auth
from langboard_shared.tasks.activities import CardCommentActivityTask
from langboard_shared.tasks.bots import CardCommentBotTask
from sqlalchemy import event, update
from sqlalchemy.orm import Session
from test_app_connection_auth_postgres import DECLARATION, connected, registered  # noqa: F401
from test_app_governance_postgres import governance  # noqa: F401


def test_native_sdk_interaction_human_cas_outbox_and_current_fences(registered, monkeypatch):  # noqa: F811
    engine, (admin, owner, outsider), connection, shared = registered
    for model in (
        WorkflowStageDefinition,
        ProjectColumn,
        Card,
        Bot,
        CardComment,
        CardCommentReaction,
        CardAppOwnership,
        CardAppOwnershipAudit,
        AppResourceBinding,
        CardAppResourceSelection,
        CardAppResourceSelectionAudit,
        AppCardInteraction,
        AppCardInteractionResponse,
        AppExecutionOutbox,
        AppEventDestination,
        WebhookSetting,
        AppExecutionAcknowledgment,
        ProjectRole,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["capabilities"] += ["cards.create", "resources.read", "interactions.request", "events.receive"]
        declaration["trust"] = {
            "authentication_origins": [],
            "mcp_origins": [],
            "data_origins": ["https://example.test"],
        }
        declaration["card_lifecycle"] = {
            "issue": {"archive": {"mode": "retain", "description": "Retain"}, "restore": "retain_external_state"}
        }
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.execute(update(BoardAppBinding).values(granted_capabilities=declaration["capabilities"]))
        db.add(WorkflowStageDefinition(id=201, key="ready", name="Ready", is_builtin=True))
        db.flush()
        db.add(ProjectColumn(id=211, project_id=20, name="Queue", workflow_stage="ready"))
        db.flush()
        db.add(Card(id=221, project_id=20, project_column_id=211, title="Example"))
        db.commit()
        app_revision = db.query(AppDefinition).one().edit_revision()
        binding_revision = db.query(BoardAppBinding).one().edit_revision()
    ids = (SnowflakeID(20), SnowflakeID(221))
    set_card_app_ownership(owner, *ids, "example", None, app_revision)
    initial = read_card_app_resources(owner, *ids, "example", connection.id)
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
        "example",
        connection.id,
        [SnowflakeID(301).to_short_code()],
        None,
        1,
        initial["identity_revision"],
        app_revision,
        binding_revision,
    )
    credential = issue_connection_credential(owner, connection.id)
    token = credential["token"]
    body = {
        "schema_version": 1,
        "kind": "approval",
        "app_request_key": "approval_one",
        "prompt": {"en": "Approve?"},
        "questions": [
            {
                "id": "decision",
                "type": "single_choice",
                "prompt": {"en": "Decision"},
                "choices": [{"id": "approve", "label": {"en": "Approve"}}, {"id": "reject", "label": {"en": "Reject"}}],
            }
        ],
        "presentation": {"type": "card_action"},
        "expires_at": (SafeDateTime.now() + timedelta(hours=1)).isoformat(),
    }
    with Session(engine) as db:
        db.add(CardComment(id=401, card_id=ids[1], content={"content": "Approve here"}))
        db.commit()
    reaction_body = deepcopy(body)
    reaction_body["app_request_key"] = "native_reaction"
    reaction_body["presentation"] = {
        "type": "comment_reaction",
        "comment_uid": SnowflakeID(401).to_short_code(),
        "reactions": {"thumbs-up": "approve", "thumbs-down": "reject"},
    }
    with Session(engine) as db:
        db.add(CardCommentReaction(id=402, comment_id=401, user_id=owner.id, reaction_type="thumbs-up"))
        db.commit()
    reaction_request = create_app_card_interaction(token, *ids, reaction_body)
    reaction_id = SnowflakeID.from_short_code(reaction_request["interaction_uid"])
    assert accept_current_comment_reaction(owner, *ids, SnowflakeID(401), "thumbs-up") is None
    duplicate = deepcopy(reaction_body)
    duplicate["app_request_key"] = "ambiguous_reaction"
    with pytest.raises(AppGovernanceConflict):
        create_app_card_interaction(token, *ids, duplicate)
    with pytest.raises(AppGovernanceDenied):
        respond_to_app_card_interaction(
            owner, *ids, reaction_id, {"decision": {"choice_id": "approve"}}, 1, "human-direct-key-001"
        )
    with Session(engine) as db:
        db.delete(db.get(CardCommentReaction, 402))
        db.add(CardCommentReaction(id=403, comment_id=401, user_id=owner.id, reaction_type="eyes"))
        db.commit()
    assert accept_current_comment_reaction(owner, *ids, SnowflakeID(401), "eyes") is None
    assert accept_current_comment_reaction(outsider, *ids, SnowflakeID(401), "thumbs-up") is None
    bot = Bot(
        id=500,
        name="Example bot",
        bot_uname="example",
        app_api_token="test",
        platform="default",
        platform_running_type="default",
    )
    assert accept_current_comment_reaction(bot, *ids, SnowflakeID(401), "thumbs-up") is None
    # Suppress outbound notifications/tasks only; keep the real reaction repository,
    # current ACL/configuration checks and PostgreSQL transaction intact.
    monkeypatch.setattr(CardCommentPublisher, "reacted", lambda *args: None)
    for task in (CardCommentActivityTask, CardCommentBotTask):
        monkeypatch.setattr(task, "card_comment_reacted", lambda *args: None)
        monkeypatch.setattr(task, "card_comment_unreacted", lambda *args: None)
    service = DomainService()
    original_insert = DbSession.insert

    def fail_reaction_outbox(db, obj):
        if isinstance(obj, AppExecutionOutbox):
            raise RuntimeError("reaction event enqueue failure")
        return original_insert(db, obj)

    with monkeypatch.context() as failure:
        failure.setattr(DbSession, "insert", fail_reaction_outbox)
        with pytest.raises(RuntimeError, match="reaction event enqueue failure"):
            service.card_comment.toggle_reaction(owner, *ids, SnowflakeID(401), "thumbs-up")
    with Session(engine) as db:
        assert db.query(CardCommentReaction).filter_by(comment_id=401, reaction_type="thumbs-up").count() == 0
        assert db.query(AppCardInteractionResponse).filter_by(interaction_id=reaction_id).count() == 0
        assert db.get(AppCardInteraction, reaction_id).revision == 1
    assert service.card_comment.toggle_reaction(owner, *ids, SnowflakeID(401), "thumbs-up") is True
    accepted = read_app_card_interaction(token, *ids, reaction_id)
    assert accepted["response"]["decision"] == "approved"
    assert not accepted["execution_resumed"]
    assert service.card_comment.toggle_reaction(owner, *ids, SnowflakeID(401), "thumbs-up") is False
    retained = read_app_card_interaction(token, *ids, reaction_id)
    assert retained["response"] == accepted["response"]
    assert accept_current_comment_reaction(owner, *ids, SnowflakeID(401), "thumbs-up") is None
    with Session(engine) as db:
        # Preserve earlier event-count assertions in the existing vertical test.
        db.query(AppExecutionOutbox).delete()
        db.query(AppCardInteractionResponse).delete()
        db.query(AppCardInteraction).delete()
        db.commit()
    Cache.clear()
    Auth.reset_user(owner)
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-interaction-secret-32bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:

        async def sdk_create():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {token}"},
            ) as session:
                return await AppCardInteractions(HttpTransport(session)).create(*(i.to_short_code() for i in ids), body)

        created = asyncio.run(sdk_create())
        assert created["revision"] == 1 and created["response"] is None and created["changed"] is True
        assert asyncio.run(sdk_create())["changed"] is False
        uid = SnowflakeID.from_short_code(created["interaction_uid"])
        statements = []

        def record_query(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        event.listen(engine, "before_cursor_execute", record_query)
        try:
            assert read_app_card_interaction(token, *ids, uid)["state"] == "pending"
        finally:
            event.remove(engine, "before_cursor_execute", record_query)
        assert not any("FOR UPDATE" in statement for statement in statements)

        with pytest.raises(AppGovernanceConflict):
            create_app_card_interaction(token, *ids, {**body, "prompt": {"en": "Changed"}})
        values = {"decision": {"choice_id": "approve"}}
        args = (*ids, uid, values, 1, "response-key-00001")
        with pytest.raises(AppGovernanceDenied):
            respond_to_app_card_interaction(outsider, *args)
        with TestClient(app) as client:
            path = f"/board/{ids[0].to_short_code()}/card/{ids[1].to_short_code()}/interactions/{uid.to_short_code()}/responses"
            response_body = {"values": values, "expected_revision": 1, "idempotency_key": "response-key-00001"}
            assert (
                client.post(path, headers={"Authorization": f"Bearer {token}"}, json=response_body).status_code == 401
            )
            access, refresh = AuthSecurity.authenticate(owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            assert client.post(path, headers=headers, json={**response_body, "actor_id": 1}).status_code in (400, 422)
            # One durable answer/event under concurrent authorized human requests.
            with ThreadPoolExecutor(max_workers=2) as pool:
                receipts = list(pool.map(lambda _: respond_to_app_card_interaction(owner, *args), range(2)))
            assert sorted(r["changed"] for r in receipts) == [False, True]
            assert len({r["response"]["response_uid"] for r in receipts}) == 1
            assert all(
                r["response"]["decision"] == "approved"
                and not r["execution_resumed"]
                and not r["workflow_transitioned"]
                for r in receipts
            )
            recovered = client.post(path, headers=headers, json=response_body)
            assert recovered.status_code == 200 and recovered.json()["changed"] is False
            assert recovered.headers["cache-control"] == "no-store"
            assert (
                client.post(
                    path, headers=headers, json={**response_body, "values": {"decision": {"choice_id": "reject"}}}
                ).status_code
                == 409
            )
            assert (
                client.get(path.removesuffix("/responses"), headers=headers).json()["response"]["decision"]
                == "approved"
            )

        async def sdk_read():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {token}"},
            ) as session:
                return await AppCardInteractions(HttpTransport(session)).read(
                    *(i.to_short_code() for i in ids), uid.to_short_code()
                )

        readback = asyncio.run(sdk_read())
        assert readback["response"]["values"] == values
        assert readback["response"]["response_uid"] == receipts[0]["response"]["response_uid"]
        with Session(engine) as db:
            assert db.query(AppCardInteractionResponse).count() == 1
            outbox = db.query(AppExecutionOutbox).one()
            assert outbox.event_type == "io.langboard.app.interaction.answered.v1" and outbox.state == "pending"
            assert outbox.payload["response_uid"] == receipts[0]["response"]["response_uid"]
            assert outbox.payload["state"] == "answered"
            assert outbox.payload["actor_uid"] == owner.get_uid()
            assert outbox.payload["app_key"] == "example"
            assert outbox.payload["connection_uid"] == connection.get_uid()
            assert outbox.payload["values"] == values
            assert outbox.payload["execution"] is None

            event_uid = outbox.get_uid()
        with Session(engine) as db:
            db.add(
                WebhookSetting(
                    id=601,
                    name="Example event receiver",
                    url="https://example.test/events",
                    secret_id="fixture-secret-ref",
                    events=["io.langboard.app.interaction.answered.v1"],
                )
            )
            db.commit()
        with pytest.raises(AppGovernanceDenied):
            bind_app_event_destination(owner, connection.id, SnowflakeID(601), None)
        target = bind_app_event_destination(admin, connection.id, SnowflakeID(601), None)
        assert target["revision"] == 1
        assert bind_app_event_destination(admin, connection.id, SnowflakeID(601), 1)["revision"] == 1
        with pytest.raises(AppGovernanceConflict):
            bind_app_event_destination(admin, connection.id, SnowflakeID(601), None)
        monkeypatch.setattr(
            KeyVault,
            "get_key",
            lambda reference: "fixture-signing-secret" if reference == "fixture-secret-ref" else None,
        )
        event_id = SnowflakeID.from_short_code(event_uid)
        destination_path = f"/settings/apps/connections/{connection.get_uid()}/event-destination"
        with TestClient(app) as client:
            assert client.get(destination_path, headers={"Authorization": f"Bearer {token}"}).status_code == 401
            Auth.reset_user(admin)
            admin_access, admin_refresh = AuthSecurity.authenticate(admin.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, admin_refresh)
            reply = client.get(destination_path, headers={"Authorization": f"Bearer {admin_access}"})
            assert reply.status_code == 200 and reply.json()["revision"] == 1
            assert "secret" not in reply.text
        disabled = manage_app_event_destination(admin, connection.id, expected_revision=1, disable=True)
        assert disabled["revision"] == 2 and not disabled["enabled"]
        with pytest.raises(AppGovernanceDenied):
            prepare_app_event_signature(event_id, 2)
        assert bind_app_event_destination(admin, connection.id, SnowflakeID(601), 2)["revision"] == 3
        with Session(engine) as db:
            prior_payload = db.query(AppExecutionOutbox).one().payload
            db.execute(
                update(AppExecutionOutbox).values(
                    payload={**prior_payload, "values": {"decision": {"choice_id": "reject"}}}
                )
            )
            db.commit()
        with pytest.raises(AppGovernanceConflict):
            prepare_app_event_signature(event_id, 3)
        with Session(engine) as db:
            db.execute(update(AppExecutionOutbox).values(payload=prior_payload))
            db.commit()
        prepared = prepare_app_event_signature(event_id, 3, timestamp=1700000000)
        verify_webhook(prepared["body"], prepared["headers"], "fixture-signing-secret", now=1700000000)
        with Session(engine) as db:
            db.execute(update(WebhookSetting).values(url="https://example.test/changed"))
            db.commit()
        with pytest.raises(AppGovernanceConflict):
            prepare_app_event_signature(event_id, 3)
        with Session(engine) as db:
            db.execute(update(WebhookSetting).values(url="https://example.test/events"))
            db.commit()
        ack_path = f"/apps/v1/boards/{ids[0].to_short_code()}/cards/{ids[1].to_short_code()}/interactions/{uid.to_short_code()}/acknowledgments"

        async def sdk_ack(reference="runtime-receipt-1"):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {token}"},
            ) as session:
                return await AppCardInteractions(HttpTransport(session)).acknowledge(
                    *(i.to_short_code() for i in ids),
                    uid.to_short_code(),
                    event_uid=event_uid,
                    runtime_reference=reference,
                )

        with TestClient(app) as client:
            auth = {"Authorization": f"Bearer {token}"}
            assert client.get(ack_path).status_code == 401
            assert client.get(ack_path, headers=auth).json()["state"] == "not_received"
            pending = client.post(
                ack_path, headers=auth, json={"event_uid": event_uid, "runtime_reference": "runtime-receipt-1"}
            )
            assert pending.status_code == 403
        with Session(engine) as db:
            db.execute(update(AppExecutionOutbox).values(state="delivering"))
            db.commit()

        async def sdk_read_ack():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {token}"},
            ) as session:
                return await AppCardInteractions(HttpTransport(session)).read_acknowledgment(
                    *(i.to_short_code() for i in ids), uid.to_short_code()
                )

        with Session(engine) as db:
            saved_payload = db.query(AppExecutionOutbox).one().payload
            db.execute(
                update(AppExecutionOutbox).values(
                    payload={**saved_payload, "values": {"decision": {"choice_id": "reject"}}}
                )
            )
            db.commit()
        with TestClient(app) as client:
            assert (
                client.post(
                    ack_path,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"event_uid": event_uid, "runtime_reference": "runtime-receipt-1"},
                ).status_code
                == 403
            )
        with Session(engine) as db:
            assert db.query(AppExecutionAcknowledgment).count() == 0
            db.execute(update(AppExecutionOutbox).values(payload=saved_payload))
            db.commit()
        first_ack = asyncio.run(sdk_ack())
        assert first_ack["changed"] and first_ack["state"] == "received"
        assert not first_ack["execution_resumed"] and not first_ack["workflow_transitioned"]
        assert asyncio.run(sdk_ack())["changed"] is False
        assert asyncio.run(sdk_read_ack())["acknowledgment_uid"] == first_ack["acknowledgment_uid"]
        with TestClient(app) as client:
            auth = {"Authorization": f"Bearer {token}"}
            recovered_ack = client.get(ack_path, headers=auth)
            assert recovered_ack.status_code == 200
            assert recovered_ack.json()["acknowledgment_uid"] == first_ack["acknowledgment_uid"]
            assert recovered_ack.headers["cache-control"] == "no-store"
            assert (
                client.post(
                    ack_path, headers=auth, json={"event_uid": event_uid, "runtime_reference": "different"}
                ).status_code
                == 409
            )
            assert (
                client.post(
                    ack_path,
                    headers=auth,
                    json={"event_uid": uid.to_short_code(), "runtime_reference": "runtime-receipt-1"},
                ).status_code
                == 403
            )
        statements = []
        event.listen(engine, "before_cursor_execute", record_query)
        try:
            with TestClient(app) as client:
                assert client.get(ack_path, headers={"Authorization": f"Bearer {token}"}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record_query)
        assert not any("FOR UPDATE" in statement for statement in statements)
        with Session(engine) as db:
            assert db.query(AppExecutionAcknowledgment).count() == 1
            db.execute(update(AppResourceBinding).values(access_state="revoked"))
            db.commit()
        with pytest.raises(AppGovernanceDenied):
            respond_to_app_card_interaction(owner, *args)
        with pytest.raises(AppGovernanceDenied):
            prepare_app_event_signature(event_id, 3)
        with TestClient(app) as client:
            assert client.get(ack_path, headers={"Authorization": f"Bearer {token}"}).status_code == 403
        with Session(engine) as db:
            db.execute(update(AppResourceBinding).values(access_state="granted"))
            db.commit()
        with Session(engine) as db:
            db.query(AppExecutionAcknowledgment).delete()
            db.commit()
        # Enqueue failure rolls answer and revision back together.
        second = create_app_card_interaction(token, *ids, {**body, "app_request_key": "approval_two"})
        real_insert = DbSession.insert

        def fail_outbox(self, item):
            if isinstance(item, AppExecutionOutbox):
                raise RuntimeError("Simulated enqueue failure")
            return real_insert(self, item)

        with monkeypatch.context() as patch:
            patch.setattr(DbSession, "insert", fail_outbox)
            with pytest.raises(RuntimeError):
                respond_to_app_card_interaction(
                    owner, *ids, SnowflakeID.from_short_code(second["interaction_uid"]), values, 1, "response-key-00002"
                )
        with Session(engine) as db:
            assert db.query(AppCardInteractionResponse).count() == 1
            assert db.get(AppCardInteraction, SnowflakeID.from_short_code(second["interaction_uid"])).revision == 1
        second_uid = SnowflakeID.from_short_code(second["interaction_uid"])
        cancelled = cancel_app_card_interaction(token, *ids, second_uid, 1)
        assert cancelled["state"] == "cancelled" and cancelled["revision"] == 2
        assert cancel_app_card_interaction(token, *ids, second_uid, 1)["changed"] is False
        with pytest.raises(AppGovernanceConflict):
            respond_to_app_card_interaction(owner, *ids, second_uid, values, 1, "response-key-00002")
        third = create_app_card_interaction(token, *ids, {**body, "app_request_key": "approval_three"})
        third_uid = SnowflakeID.from_short_code(third["interaction_uid"])
        with Session(engine) as db:
            db.execute(
                update(AppCardInteraction)
                .where(AppCardInteraction.id == third_uid)
                .values(expires_at=SafeDateTime.now() - timedelta(seconds=1))
            )
            db.commit()
        assert read_app_card_interaction(token, *ids, third_uid)["state"] == "expired"
        with pytest.raises(AppGovernanceConflict):
            respond_to_app_card_interaction(owner, *ids, third_uid, values, 1, "response-key-00003")
        fourth = create_app_card_interaction(token, *ids, {**body, "app_request_key": "approval_four"})
        with Session(engine) as db:
            db.execute(update(CardAppOwnership).values(revision=2))
            db.commit()
        with pytest.raises(AppGovernanceDenied):
            respond_to_app_card_interaction(
                owner, *ids, SnowflakeID.from_short_code(fourth["interaction_uid"]), values, 1, "response-key-00004"
            )
        with Session(engine) as db:
            db.execute(update(CardAppOwnership).values(revision=1))
            db.commit()
        question = create_app_card_interaction(
            token, *ids, {**body, "kind": "question", "app_request_key": "question_one"}
        )
        answered_question = respond_to_app_card_interaction(
            owner, *ids, SnowflakeID.from_short_code(question["interaction_uid"]), values, 1, "question-key-00001"
        )
        assert answered_question["response"]["decision"] is None
        with pytest.raises(ValueError):
            respond_to_app_card_interaction(owner, *ids, third_uid, values, 1, "response-key-00003", True)
        with Session(engine) as db:
            previous_events = db.query(AppExecutionOutbox).count()
            db.execute(
                update(BoardAppBinding).values(
                    granted_capabilities=[c for c in declaration["capabilities"] if c != "events.receive"]
                )
            )
            db.commit()
        local_only = create_app_card_interaction(token, *ids, {**body, "app_request_key": "local_only"})
        assert (
            respond_to_app_card_interaction(
                owner, *ids, SnowflakeID.from_short_code(local_only["interaction_uid"]), values, 1, "local-answer-00001"
            )["state"]
            == "answered"
        )
        with Session(engine) as db:
            assert db.query(AppExecutionOutbox).count() == previous_events
            db.execute(update(BoardAppBinding).values(granted_capabilities=declaration["capabilities"]))
            db.commit()
        with Session(engine) as db:
            declaration["workflow_requirements"] = {"required": ["ready"], "optional": []}
            db.execute(update(AppDefinition).values(declaration=declaration))
            db.execute(
                update(BoardAppBinding).values(
                    stage_transitions_enabled=True, workflow_mapping={"ready": SnowflakeID(211).to_short_code()}
                )
            )
            db.commit()
        mapped = create_app_card_interaction(
            token,
            *ids,
            {**body, "app_request_key": "workflow_one", "outcomes": {"approve": {"workflow_stage": "ready"}}},
        )
        with Session(engine) as db:
            db.execute(update(ProjectColumn).values(updated_at=SafeDateTime.now() + timedelta(seconds=1)))
            db.commit()
        with pytest.raises(AppGovernanceConflict):
            respond_to_app_card_interaction(
                owner, *ids, SnowflakeID.from_short_code(mapped["interaction_uid"]), values, 1, "workflow-key-00001"
            )
        revoke_connection_credential(owner, connection.id, SnowflakeID.from_short_code(credential["credential_uid"]))
        with pytest.raises(AppGovernanceDenied):
            create_app_card_interaction(token, *ids, {**body, "app_request_key": "approval_three"})
    finally:
        Cache.clear()
