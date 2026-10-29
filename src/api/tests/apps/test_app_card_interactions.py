# ruff: noqa: F811
"""Native app request persistence, dedupe, and revocation without runtime effects."""

import importlib
from datetime import timedelta
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppCardInteractionApi, CardHumanInteractionApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import AppCardInteraction, AppDefinition, BoardAppBinding
from test_app_execution_ack import scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("app_profile", ["baseline", "example-monitor"])
def test_native_app_owned_choice_creation_read_dedupe_and_denial(board, monkeypatch, app_profile):
    from langboard_shared.core.broker import Broker

    enqueues = []
    def enqueue(name, *, args):
        assert name == "langboard.app_event_delivery"
        enqueues.append(args)
    monkeypatch.setattr(Broker.celery, "send_task", enqueue)
    if app_profile == "example-monitor":
        from packaged_monitor import load_monitor
        Monitor = load_monitor(monkeypatch, "example-monitor-hitl-provenance.json")
        import test_app_connection_resources as resources_fixture
        original_setup = resources_fixture.setup
        def monitor_setup(actor):
            connection, definition = original_setup(actor)
            with DbSession.atomic() as db:
                connection.app_key = definition.key = "example-monitor"
                definition.declaration = {**definition.declaration, "key": "example-monitor"}
                db.update(connection)
                db.update(definition)
            return connection, definition
        monkeypatch.setattr(resources_fixture, "setup", monitor_setup)
        monkeypatch.setattr(resources_fixture, "DECLARATION", {**resources_fixture.DECLARATION, "key": "example-monitor"})
    connection, card, token, _ = scope(board, monkeypatch)
    migration = importlib.import_module("langboard.migrations.versions.20261011090000-ed71a806b2f4")
    original = migration.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            migration.op = Operations(MigrationContext.configure(db))
            migration.upgrade()
    finally:
        migration.op = original
    response_migration = importlib.import_module("langboard.migrations.versions.20261011100000-fe82b917c3a5")
    original = response_migration.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            response_migration.op = Operations(MigrationContext.configure(db))
            response_migration.upgrade()
    finally:
        response_migration.op = original
    workflow_migration = importlib.import_module("langboard.migrations.versions.20261011110000-a093ca28d4b6")
    original = workflow_migration.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            workflow_migration.op = Operations(MigrationContext.configure(db))
            workflow_migration.upgrade()
    finally:
        workflow_migration.op = original
    transition_migration = importlib.import_module("langboard.migrations.versions.20261011120000-b1a4db39e5c7")
    original = transition_migration.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            transition_migration.op = Operations(MigrationContext.configure(db))
            transition_migration.upgrade()
    finally:
        transition_migration.op = original
    with DbSession.atomic() as db:
        definition = db.exec(
            SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key)
        ).first()
        definition.declaration = {
            **definition.declaration,
            "capabilities": [*definition.declaration["capabilities"], "interactions.request"],
            "workflow_requirements": {"required": ["ready"], "optional": ["review"]},
        }
        db.update(definition)
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == connection.app_key)
        ).first()
        binding.granted_capabilities = [*binding.granted_capabilities, "interactions.request"]
        binding.workflow_mapping = {**binding.workflow_mapping, "review": board[5][1].get_uid()}
        db.update(binding)
        board[6][1].is_builtin = True
        db.update(board[6][1])
    from langboard_shared.domain.models import WebhookSetting
    from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
    from langboard_shared.domain.services.AppEventDestination import (
        APP_INTERACTION_RESPONSE_EVENT,
        bind_app_event_destination,
    )

    with DbSession.atomic() as db:
        setting = db.exec(SqlBuilder.select.table(WebhookSetting)).first()
        setting.events = [*setting.events, APP_INTERACTION_RESPONSE_EVENT]
        db.update(setting)
        binding.granted_capabilities = [*binding.granted_capabilities, "events.receive"]
        db.update(binding)
    assert bind_app_event_destination(board[1], connection.id, setting.id, 1)["revision"] == 2

    original_grants = list(binding.granted_capabilities)
    token = issue_connection_credential(board[1], connection.id)["token"]
    body = {
        "schema_version": 1,
        "kind": "action",
        "app_request_key": "deployment-choice",
        "prompt": {"en": "Choose next action", "ko": "다음 작업 선택"},
        "questions": [
            {
                "id": "action",
                "prompt": {"en": "Action"},
                "type": "single_choice",
                "choices": [{"id": "review", "label": {"en": "Review"}}, {"id": "retry", "label": {"en": "Retry"}}],
            }
        ],
        "presentation": {"type": "choice_card"},
        "outcomes": {"review": {"workflow_stage": "review"}},
        "expires_at": (SafeDateTime.now() + timedelta(hours=1)).isoformat(),
    }
    app = FastAPI()
    app.include_router(AppRouter.api)
    path = f"/apps/v1/boards/{board[2].get_uid()}/cards/{card.get_uid()}/interactions"
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        assert client.post(path, json={"request": body}).status_code == 401
        first = client.post(path, json={"request": body}, headers=headers)
        assert first.status_code == 200, first.text
        receipt = first.json()
        assert receipt["state"] == "pending" and receipt["execution_resumed"] is False
        assert receipt["changed"] is True and receipt["revision"] == 1
        recovered = client.get(path, params={"app_request_key": body["app_request_key"]}, headers=headers)
        assert recovered.status_code == 200 and recovered.json()["interaction_uid"] == receipt["interaction_uid"]
        assert recovered.json()["changed"] is False
        assert client.get(path, params={"app_request_key": "missing"}, headers=headers).json()["state"] == "missing"
        assert client.get(path, params={"app_request_key": "deployment-choice"}).status_code == 401
        with DbSession.atomic() as db:
            frozen = (
                db.exec(
                    SqlBuilder.select.table(AppCardInteraction).where(
                        AppCardInteraction.app_request_key == "deployment-choice"
                    )
                )
                .first()
                .workflow_snapshot
            )
        assert frozen["choices"]["review"]["column_uid"] == board[5][1].get_uid()
        assert (
            client.post(
                path,
                json={
                    "request": {
                        **body,
                        "app_request_key": "unmapped",
                        "outcomes": {"review": {"workflow_stage": "closed"}},
                    }
                },
                headers=headers,
            ).status_code
            == 409
        )
        second = client.post(path, json={"request": body}, headers=headers).json()
        assert second["changed"] is False and second["interaction_uid"] == receipt["interaction_uid"]
        assert receipt["source"] == {"app_key": connection.app_key, "connection_uid": connection.get_uid()}
        assert (
            client.post(path, json={"request": {**body, "prompt": {"en": "Changed"}}}, headers=headers).status_code
            == 409
        )
        read_path = path + "/" + receipt["interaction_uid"]
        assert client.get(read_path, headers=headers).json()["request"] == body
        import asyncio
        from types import SimpleNamespace
        from langboard_sdk import AppCardInteractions, HttpTransport

        async def send(method, url, **kwargs):
            return client.request(method, url, headers=headers, **kwargs)

        sdk = AppCardInteractions(HttpTransport(SimpleNamespace(request=send)))
        if app_profile == "example-monitor":
            class PackagedInteractions:
                async def create(self, board_uid, card_uid, request):
                    return await Monitor.question(transport, board_uid, card_uid, request)
                async def read(self, board_uid, card_uid, interaction_uid):
                    return await Monitor.current_response(transport, board_uid, card_uid, interaction_uid)
                async def acknowledge(self, board_uid, card_uid, interaction_uid, **kwargs):
                    return await Monitor.acknowledge_response(transport, board_uid, card_uid, interaction_uid, **kwargs)
                async def cancel(self, *args, **kwargs):
                    return await AppCardInteractions(transport).cancel(*args, **kwargs)
            transport = HttpTransport(SimpleNamespace(request=send))
            sdk = PackagedInteractions()
        saved = asyncio.run(sdk.create(board[2].get_uid(), card.get_uid(), body))
        assert saved["changed"] is False
        if app_profile == "example-monitor":
            from langboard_sdk import NativeApiError
            from langboard_shared.domain.models import CardAppOwnership
            with DbSession.atomic() as db:
                ownership = db.exec(SqlBuilder.select.table(CardAppOwnership).where(
                    CardAppOwnership.card_id == card.id)).first()
                ownership.app_key = "different-owner"
                db.update(ownership)
            with pytest.raises(NativeApiError) as denied:
                asyncio.run(sdk.create(board[2].get_uid(), card.get_uid(), {**body, "app_request_key": "foreign-owner"}))
            assert denied.value.status_code == 403
            with DbSession.atomic() as db:
                ownership.app_key = connection.app_key
                db.update(ownership)

        assert asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), receipt["interaction_uid"]))["request"] == body

        mixed = {
            **body,
            "kind": "question",
            "app_request_key": "choice-or-text",
            "questions": [{**body["questions"][0], "type": "choice_or_text", "max_length": 2000}],
        }
        mixed_receipt = asyncio.run(sdk.create(board[2].get_uid(), card.get_uid(), mixed))
        assert mixed_receipt["execution_resumed"] is False
        assert (
            asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), mixed_receipt["interaction_uid"]))["request"]
            == mixed
        )
        assert client.post(path, json={"request": {**mixed, "kind": "approval"}}, headers=headers).status_code == 400
        assert (
            client.post(
                path,
                json={"request": {**mixed, "questions": [{**mixed["questions"][0], "is_secret": True}]}},
                headers=headers,
            ).status_code
            == 400
        )
        from langboard_sdk import validate_card_interaction_values

        retained = asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), mixed_receipt["interaction_uid"]))[
            "request"
        ]
        assert validate_card_interaction_values(retained, {"action": {"choice_id": "review"}}) == {
            "action": {"choice_id": "review"}
        }
        assert validate_card_interaction_values(retained, {"action": {"text": "추가 검토 필요"}}) == {
            "action": {"text": "추가 검토 필요"}
        }
        for answer in ({"choice_id": "Review"}, {"choice_id": "review", "text": "override"}, {"text": "x" * 2001}):
            with pytest.raises(ValueError):
                validate_card_interaction_values(retained, {"action": answer})
        assert client.post(path, json={"request": {**body, "kind": "unknown"}}, headers=headers).status_code == 400
        with DbSession.atomic() as db:
            binding.granted_capabilities = [c for c in binding.granted_capabilities if c != "interactions.request"]
            db.update(binding)
        assert client.get(read_path, headers=headers).status_code == 403
        assert client.get(path, params={"app_request_key": "deployment-choice"}, headers=headers).status_code == 403
        assert client.post(path, json={"request": body}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            binding.granted_capabilities = original_grants
            db.update(binding)
        with DbSession.atomic() as db:
            row = db.exec(
                SqlBuilder.select.table(AppCardInteraction).where(
                    AppCardInteraction.app_request_key == "deployment-choice"
                )
            ).first()
            row.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(row)
        assert client.get(read_path, headers=headers).json()["state"] == "expired"
        assert (
            client.post(read_path + "/cancel", json={"expected_revision": 1}, headers=headers).json()["state"]
            == "expired"
        )
        cancel_body = {**body, "app_request_key": "cancel-choice"}
        pending = client.post(path, json={"request": cancel_body}, headers=headers).json()
        cancel_path = path + "/" + pending["interaction_uid"] + "/cancel"
        assert client.post(cancel_path, json={"expected_revision": 2}, headers=headers).status_code == 409
        assert client.post(cancel_path, json={"expected_revision": True}, headers=headers).status_code == 400
        assert client.post(cancel_path, json={"expected_revision": 1}).status_code == 401
        cancelled = asyncio.run(
            sdk.cancel(board[2].get_uid(), card.get_uid(), pending["interaction_uid"], expected_revision=1)
        )
        assert cancelled["state"] == "cancelled" and cancelled["revision"] == 2 and cancelled["changed"] is True
        retry = asyncio.run(
            sdk.cancel(board[2].get_uid(), card.get_uid(), pending["interaction_uid"], expected_revision=1)
        )
        assert retry["state"] == "cancelled" and retry["changed"] is False
        assert client.post(cancel_path, json={"expected_revision": 2}, headers=headers).status_code == 409
        replay = client.post(path, json={"request": cancel_body}, headers=headers).json()
        assert replay["state"] == "cancelled" and replay["execution_resumed"] is False
        from langboard_shared.core.types import SnowflakeID
        from langboard_shared.domain.services import DomainService
        from langboard_shared.domain.services.AppCardInteractions import (
            AppCardInteractionConflict,
            respond_to_app_card_interaction,
        )
        from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied

        monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
        from langboard_shared.domain.models import EmployeeMembershipPolicy

        EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
        args = (
            DomainService(),
            board[1],
            board[2].id,
            card.id,
            SnowflakeID.from_short_code(mixed_receipt["interaction_uid"]),
        )
        answer = {"action": {"text": "추가 검토 필요"}}
        from langboard_shared.domain.services.AppCardInteractions import _validate_workflow_snapshot

        with DbSession.atomic() as db:
            saved_row = db.exec(
                SqlBuilder.select.table(AppCardInteraction).where(AppCardInteraction.id == args[-1])
            ).first()
            plan = _validate_workflow_snapshot(db, saved_row, {"action": {"choice_id": "review"}})
            assert plan["state"] == "planned" and plan["column_uid"] == board[5][1].get_uid()
            assert _validate_workflow_snapshot(db, saved_row, answer)["state"] == "no_outcome"
            binding.workflow_mapping = {**binding.workflow_mapping, "review": board[5][2].get_uid()}
            db.update(binding)
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*args, 1, "stale-mapping", {"action": {"choice_id": "review"}})
        with DbSession.atomic() as db:
            binding.workflow_mapping = {**binding.workflow_mapping, "review": board[5][1].get_uid()}
            db.update(binding)
            target = board[5][1]
            old_revision = target.updated_at
            target.updated_at = old_revision + timedelta(seconds=1)
            db.update(target)
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*args, 1, "stale-column", {"action": {"choice_id": "review"}})
        with DbSession.atomic() as db:
            target.updated_at = old_revision
            db.update(target)
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*args, 2, "response-1", answer)
        with pytest.raises(ValueError):
            respond_to_app_card_interaction(*args, 1, "response-1", {"action": {"choice_id": "Review"}})
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*args, 1, "response-1", answer, execution_generation=1)
        expired_args = (*args[:4], SnowflakeID.from_short_code(receipt["interaction_uid"]))
        cancelled_args = (*args[:4], SnowflakeID.from_short_code(pending["interaction_uid"]))
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*expired_args, 1, "response-1", {"action": {"choice_id": "review"}})
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*cancelled_args, 2, "response-1", {"action": {"choice_id": "review"}})
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.board import AppPanelDataApi, CardAppInteractionsApi  # noqa: F401
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.Env import Env

        human_app = FastAPI()
        human_app.include_router(AppRouter.api)
        human_app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        human_path = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/interactions/{mixed_receipt['interaction_uid']}/responses"
        form = {"expected_revision": 1, "idempotency_key": "response-1", "values": answer}
        with TestClient(human_app) as human_client:
            list_path = human_path.rsplit("/", 2)[0]
            assert human_client.get(list_path).status_code == 401
            assert human_client.get(list_path, headers=headers).status_code == 401
            assert human_client.post(human_path, json=form).status_code == 401
            assert human_client.post(human_path, json=form, headers=headers).status_code == 401
            access, refresh = AuthSecurity.authenticate(board[1].id)
            human_client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            human_headers = {"Authorization": f"Bearer {access}"}
            legacy_read = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/interactions/{mixed_receipt['interaction_uid']}"
            assert human_client.get(legacy_read).status_code == 401
            assert human_client.get(legacy_read, headers=headers).status_code == 401
            retained = human_client.get(legacy_read, headers=human_headers)
            assert retained.status_code == 200, retained.text
            assert retained.json()["interaction_uid"] == mixed_receipt["interaction_uid"]
            assert retained.headers["cache-control"] == "no-store"
            assert human_client.get(legacy_read.replace(mixed_receipt["interaction_uid"], "A" * 11), headers=human_headers).status_code == 403
            assert human_client.post(legacy_read + "/responses", json=form, headers=headers).status_code == 401
            assert human_client.post(legacy_read + "/responses", json={**form, "actor_uid": "A" * 11}, headers=human_headers).status_code == 400
            assert human_client.post(legacy_read + "/responses", json={**form, "expected_revision": 99}, headers=human_headers).status_code == 409
            page = human_client.get(list_path, params={"limit": 1}, headers=human_headers)
            assert page.status_code == 200, page.text
            assert page.headers["cache-control"] == "no-store"
            assert len(page.json()["items"]) == 1 and page.json()["next_cursor"]
            assert page.json()["items"][0]["source"] == receipt["source"]
            next_page = human_client.get(
                list_path, params={"limit": 1, "cursor": page.json()["next_cursor"]}, headers=human_headers
            )
            assert next_page.status_code == 200
            assert next_page.json()["items"][0]["interaction_uid"] != page.json()["items"][0]["interaction_uid"]
            assert human_client.get(list_path, params={"limit": 26}, headers=human_headers).status_code == 400
            assert human_client.get(list_path, params={"cursor": "invalid"}, headers=human_headers).status_code == 400
            assert (
                human_client.post(human_path, json={**form, "actor_uid": "A" * 11}, headers=human_headers).status_code
                == 400
            )
            # A live panel reads only its own requests, never another app's retained history.
            from langboard_shared.domain.services.AppCardInteractions import list_card_interactions
            assert list_card_interactions(args[0], board[1], board[2].id, card.id, app_key="unrelated-app")["items"] == []
            previous_declaration = dict(definition.declaration)
            previous_grants = list(binding.granted_capabilities)
            with DbSession.atomic() as db:
                definition.declaration = {**definition.declaration,
                    "capabilities": [*definition.declaration["capabilities"], "panels.render"],
                    "panel": {"url": "https://panel.example.invalid/panel", "name": "Requests", "icon": "message-square"}}
                binding.granted_capabilities = [*binding.granted_capabilities, "panels.render"]
                db.update(definition)
                db.update(binding)
            panel_path = f"/board/{board[2].get_uid()}/apps/{connection.app_key}/panel/interactions"
            panel_form = {"app_revision": definition.edit_revision(), "binding_revision": binding.edit_revision(),
                          "card_uid": card.get_uid(), "limit": 1}
            assert human_client.post(panel_path, json=panel_form, headers=headers).status_code == 401
            panel_page = human_client.post(panel_path, json=panel_form, headers=human_headers)
            assert panel_page.status_code == 200, panel_page.text
            assert panel_page.headers["cache-control"] == "no-store"
            assert len(panel_page.json()["items"]) == 1 and panel_page.json()["next_cursor"]
            assert panel_page.json()["items"][0]["source"]["app_key"] == connection.app_key
            for extra in [{"actor_uid": "A" * 11}, {"limit": True}, {"limit": 26}, {"cursor":"bad"}]:
                assert human_client.post(panel_path, json={**panel_form, **extra}, headers=human_headers).status_code == 400
            assert human_client.post(panel_path, json={**panel_form,"app_revision":"0" * 64}, headers=human_headers).status_code == 409
            assert human_client.post(panel_path, json={**panel_form,"card_uid":"A" * 11}, headers=human_headers).status_code == 404
            from langboard_shared.domain.services.AppCardInteractions import create_app_card_interaction
            panel_token = issue_connection_credential(board[1], connection.id)["token"]
            panel_request = create_app_card_interaction(panel_token, board[2].id, card.id,
                {**mixed,"app_request_key":"panel-human-answer","outcomes":{}})
            panel_answer_path = panel_path.rsplit("/",1)[0] + f"/cards/{card.get_uid()}/interactions/{panel_request['interaction_uid']}/responses"
            panel_answer_form = {**form,"app_revision":definition.edit_revision(),"binding_revision":binding.edit_revision()}
            assert human_client.post(panel_answer_path,json=panel_answer_form,headers=headers).status_code == 401
            assert human_client.post(panel_answer_path,json={**panel_answer_form,"actor_uid":"A"*11},headers=human_headers).status_code == 400
            assert human_client.post(panel_answer_path,json={**panel_answer_form,"binding_revision":"0"*64},headers=human_headers).status_code == 409
            with DbSession.atomic() as db:
                isolated = db.exec(SqlBuilder.select.table(AppCardInteraction).where(
                    AppCardInteraction.app_request_key == "panel-human-answer")).first()
                isolated.app_key = "other-app"
                db.update(isolated)
            assert human_client.post(panel_answer_path,json=panel_answer_form,headers=human_headers).status_code == 404
            with DbSession.atomic() as db:
                isolated.app_key = connection.app_key
                db.update(isolated)
            native_panel_answer = human_client.post(panel_answer_path,json=panel_answer_form,headers=human_headers)
            assert native_panel_answer.status_code == 200, native_panel_answer.text
            assert native_panel_answer.json()["state"] == "answered" and native_panel_answer.json()["execution_resumed"] is False
            assert human_client.post(panel_answer_path,json=panel_answer_form,headers=human_headers).json()["changed"] is False
            selected_page = human_client.post(panel_path,json={**panel_form,"interaction_uid":panel_request["interaction_uid"]},headers=human_headers)
            assert selected_page.json()["items"][0]["response"]["values"] == answer
            with DbSession.atomic() as db:
                binding.granted_capabilities = [value for value in binding.granted_capabilities if value != "panels.render"]
                db.update(binding)
            assert human_client.post(panel_path, json=panel_form, headers=human_headers).status_code == 404
            assert human_client.post(panel_answer_path,json=panel_answer_form,headers=human_headers).status_code == 404
            with DbSession.atomic() as db:
                definition.declaration = previous_declaration
                binding.granted_capabilities = previous_grants
                db.update(definition)
                db.update(binding)
            concurrent = DbEngine.get_main_engine().dialect.name == "postgresql"
            if concurrent:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(
                        pool.map(lambda _: respond_to_app_card_interaction(*args, 1, "response-1", answer), range(4))
                    )
                assert sum(result["changed"] for result in results) == 1
                assert len({result["response_uid"] for result in results}) == 1
            accepted_response = human_client.post(human_path, json=form, headers=human_headers)
            assert accepted_response.status_code == 200, accepted_response.text
            accepted = accepted_response.json()
            assert accepted_response.headers["cache-control"] == "no-store"
            assert human_client.post(human_path, json=form, headers=human_headers).json()["changed"] is False
            listed = human_client.get(list_path, headers=human_headers).json()["items"]
            legacy_replay = human_client.post(legacy_read + "/responses", json=form, headers=human_headers)
            assert legacy_replay.status_code == 200, legacy_replay.text
            assert legacy_replay.json()["changed"] is False and legacy_replay.json()["execution_resumed"] is False
            listed_answer = next(row for row in listed if row["interaction_uid"] == mixed_receipt["interaction_uid"])
            assert listed_answer["response"]["values"] == answer
            assert listed_answer["state"] == "answered" and listed_answer["delivery_state"] == "pending"
            assert listed_answer["execution_resumed"] is False
        assert accepted["state"] == "answered" and accepted["revision"] == 2
        assert accepted["actor_uid"] == board[1].get_uid() and accepted["changed"] is (not concurrent)
        assert accepted["execution_resumed"] is False and accepted["workflow_transitioned"] is False
        assert accepted["workflow_plan"] == {"state": "no_outcome"}
        assert respond_to_app_card_interaction(*args, 1, "response-1", answer)["changed"] is False
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*args, 1, "response-1", {"action": {"choice_id": "retry"}})
        with pytest.raises(AppCardInteractionConflict):
            respond_to_app_card_interaction(*args, 1, "response-2", answer)
        answered = asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), mixed_receipt["interaction_uid"]))
        assert answered["state"] == "answered"
        assert answered["response"]["response_uid"] == accepted["response_uid"]
        assert answered["response"]["values"] == answer
        assert answered["response"]["actor_uid"] == board[1].get_uid()
        from langboard_shared.domain.services.AppCardInteractions import acknowledge_app_interaction_response
        from langboard_shared.domain.services.AppEventDelivery import (
            claim_app_event,
            finish_app_event,
            revalidate_app_event_claim,
        )

        assert len(enqueues) == 1 and int(enqueues[0][0]) == int(SnowflakeID.from_short_code(accepted["event_uid"]))
        event_id = SnowflakeID.from_short_code(accepted["event_uid"])
        assert accepted["delivery_state"] == "pending" and accepted["reception_state"] == "not_received"
        with pytest.raises(AppGovernanceDenied):
            acknowledge_app_interaction_response(token, board[2].id, card.id, args[-1], event_id, "runtime-1")
        claim = claim_app_event(event_id, 2)
        assert claim is not None and claim["event_uid"] == accepted["event_uid"]
        import hmac
        import json
        from hashlib import sha256

        stamp = claim["headers"]["X-Langboard-Webhook-Timestamp"]
        digest = hmac.new(b"secret", stamp.encode() + b"." + claim["body"], sha256).hexdigest()
        assert claim["headers"]["X-Langboard-Webhook-Signature"] == "v1=" + digest
        from langboard_sdk import verify_webhook

        verify_webhook(claim["body"], claim["headers"], "secret")
        payload = json.loads(claim["body"])
        assert payload["response_uid"] == accepted["response_uid"] and payload["values"] == answer
        assert payload["execution_resumed"] is False
        assert revalidate_app_event_claim(event_id, claim["claim_token"], 2)["body"] == claim["body"]
        failed = finish_app_event(event_id, claim["claim_token"], delivered=False)
        assert failed["state"] == "pending"
        retry_claim = claim_app_event(event_id, 2)
        assert retry_claim["event_uid"] == claim["event_uid"] and retry_claim["attempt_count"] == 2
        assert retry_claim["claim_token"] != claim["claim_token"]
        from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict

        with pytest.raises(AppGovernanceConflict):
            finish_app_event(event_id, claim["claim_token"], delivered=True)
        claim = retry_claim
        ack_path = path + "/" + mixed_receipt["interaction_uid"] + "/acknowledgments"
        absent_ack = client.get(ack_path, headers=headers)
        assert absent_ack.status_code == 200 and absent_ack.json()["state"] == "not_received"
        assert absent_ack.json()["changed"] is False and absent_ack.json()["execution_resumed"] is False
        ack = client.post(
            ack_path, json={"event_uid": accepted["event_uid"], "runtime_reference": "runtime-1"}, headers=headers
        )
        assert ack.status_code == 200, ack.text
        reception = client.get(ack_path, headers=headers)
        assert reception.status_code == 200, reception.text
        assert reception.json()["acknowledgment_uid"] == ack.json()["acknowledgment_uid"]
        assert reception.json()["changed"] is False and reception.json()["execution_resumed"] is False
        assert ack.json()["state"] == "received" and ack.json()["execution_resumed"] is False
        sdk_ack = asyncio.run(
            sdk.acknowledge(
                board[2].get_uid(),
                card.get_uid(),
                mixed_receipt["interaction_uid"],
                event_uid=accepted["event_uid"],
                runtime_reference="runtime-1",
            )
        )
        assert sdk_ack["acknowledgment_uid"] == ack.json()["acknowledgment_uid"] and sdk_ack["changed"] is False
        assert sdk_ack["execution_resumed"] is False
        assert (
            client.post(
                ack_path, json={"event_uid": accepted["event_uid"], "runtime_reference": "runtime-1"}, headers=headers
            ).json()["changed"]
            is False
        )
        assert (
            client.post(
                ack_path, json={"event_uid": accepted["event_uid"], "runtime_reference": "runtime-2"}, headers=headers
            ).status_code
            == 409
        )
        assert finish_app_event(event_id, claim["claim_token"], delivered=True)["state"] == "delivered"
        readback = asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), mixed_receipt["interaction_uid"]))
        assert readback["delivery_state"] == "delivered" and readback["reception_state"] == "received"
        assert readback["execution_resumed"] is False
        from unittest.mock import AsyncMock
        from langboard_shared.tasks.webhooks import AppEventDeliveryWorker

        worker_mock = AsyncMock(return_value={"state": "delivered"})
        monkeypatch.setattr(AppEventDeliveryWorker, "deliver_app_event", worker_mock)
        assert asyncio.run(AppEventDeliveryWorker.recover_app_events(25)) == 0
        worker_mock.assert_not_awaited()
        from langboard_shared.domain.models import AppExecutionOutbox

        with DbSession.atomic() as db:
            retained_event = db.exec(
                SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.id == event_id)
            ).first()
            retained_event.state = "pending"
            db.update(retained_event)
        assert asyncio.run(AppEventDeliveryWorker.recover_app_events(1)) == 1
        worker_mock.assert_awaited_once_with(event_id, 2)
        with DbSession.atomic() as db:
            retained_event.state = "delivered"
            db.update(retained_event)

        from langboard_shared.domain.models import CardComment, CardCommentReaction
        from langboard_shared.domain.services.AppCardInteractions import accept_current_comment_reaction

        CardComment.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
        CardCommentReaction.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
        with DbSession.atomic() as db:
            comment = CardComment(card_id=card.id)
            db.insert(comment)
            old_reaction = CardCommentReaction(
                comment_id=comment.id,
                user_id=board[1].id,
                reaction_type="thumbs-up",
                created_at=SafeDateTime.now() - timedelta(minutes=1),
            )
            db.insert(old_reaction)
        reaction_body = {
            **body,
            "kind": "approval",
            "app_request_key": "reaction-approval",
            "outcomes": {"approve": {"workflow_stage": "review"}},
            "questions": [
                {
                    "id": "approval",
                    "prompt": {"en": "Approve?"},
                    "type": "single_choice",
                    "choices": [
                        {"id": "approve", "label": {"en": "Approve"}},
                        {"id": "reject", "label": {"en": "Reject"}},
                    ],
                }
            ],
            "presentation": {
                "type": "comment_reaction",
                "comment_uid": comment.get_uid(),
                "reactions": {"thumbs-up": "approve", "thumbs-down": "reject"},
            },
        }
        reaction_receipt = asyncio.run(sdk.create(board[2].get_uid(), card.get_uid(), reaction_body))
        assert (
            client.post(
                path, json={"request": {**reaction_body, "app_request_key": "competing-reaction"}}, headers=headers
            ).status_code
            == 409
        )
        reaction_args = (*args[:4], SnowflakeID.from_short_code(reaction_receipt["interaction_uid"]))
        with pytest.raises(AppGovernanceDenied):
            respond_to_app_card_interaction(
                *reaction_args, 1, "forged-reaction", {"approval": {"choice_id": "approve"}}
            )
        native_service = DomainService()
        assert (
            accept_current_comment_reaction(native_service, board[1], board[2].id, card.id, comment.id, "thumbs-up")
            is None
        )
        assert (
            accept_current_comment_reaction(native_service, board[1], board[2].id, card.id, comment.id, "eyes") is None
        )
        comment_module = importlib.import_module("langboard_shared.domain.services.factory.CardCommentService")
        monkeypatch.setattr(comment_module.CardCommentPublisher, "reacted", lambda *a, **k: None)
        for tasks in (comment_module.CardCommentActivityTask, comment_module.CardCommentBotTask):
            monkeypatch.setattr(tasks, "card_comment_reacted", lambda *a, **k: None)
            monkeypatch.setattr(tasks, "card_comment_unreacted", lambda *a, **k: None)
        assert native_service.card_comment.toggle_reaction(board[1], board[2], card, comment, "thumbs-up") is False
        assert (
            asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), reaction_receipt["interaction_uid"]))["state"]
            == "pending"
        )
        assert native_service.card_comment.toggle_reaction(board[1], board[2], card, comment, "thumbs-up") is True
        approved = asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), reaction_receipt["interaction_uid"]))
        assert approved["state"] == "answered" and approved["response"]["values"] == {
            "approval": {"choice_id": "approve"}
        }
        assert approved["execution_resumed"] is False
        assert approved["workflow_transitioned"] is False
        assert approved["workflow_plan"]["state"] == "planned"
        assert approved["workflow_plan"]["column_uid"] == board[5][1].get_uid()
        with DbSession.atomic() as db:
            assert (
                db.exec(SqlBuilder.select.table(type(card)).where(type(card).id == card.id)).first().project_column_id
                == board[5][0].id
            )
        assert len(enqueues) == 2
        assert native_service.card_comment.toggle_reaction(board[1], board[2], card, comment, "thumbs-up") is False
        retained = asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), reaction_receipt["interaction_uid"]))
        assert retained["response"]["response_uid"] == approved["response"]["response_uid"]
        assert native_service.card_comment.toggle_reaction(board[1], board[2], card, comment, "thumbs-up") is True
        assert len(enqueues) == 2
        response_event = SnowflakeID.from_short_code(approved["event_uid"])
        response_claim = claim_app_event(response_event, 2)
        assert response_claim is not None
        assert finish_app_event(response_event, response_claim["claim_token"], delivered=True)["state"] == "delivered"
        if DbEngine.get_main_engine().dialect.name == "postgresql":
            from langboard_shared.domain.models import (
                AppCardInteractionTransition,
                CardChangeReceipt,
                CardDecisionGate,
                DecisionCase,
                ProjectExecutionBinding,
            )
            from langboard_shared.domain.services.AppInteractionTransitions import apply_interaction_transition
            from sqlalchemy import text

            ProjectExecutionBinding.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
            # Native movement reads optional gates even when none are configured.
            # Preserve their real foreign-key dependency on immutable decisions.
            DecisionCase.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
            CardDecisionGate.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
            CardChangeReceipt.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
            with DbEngine.get_main_engine().begin() as sql:
                sql.execute(text("CREATE SEQUENCE content_change_seq START 100"))
            moves = []
            monkeypatch.setattr(native_service.card, "notify_order_changed", lambda *args: moves.append(args))
            transition_args = (
                native_service,
                board[1],
                board[2].id,
                card.id,
                reaction_args[-1],
                SnowflakeID.from_short_code(approved["response"]["response_uid"]),
            )
            with pytest.raises(AppCardInteractionConflict):
                apply_interaction_transition(*transition_args, 999)
            from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService

            policy = native_service.card._get_service(WorkflowStagePolicyService)
            with monkeypatch.context() as failed_move:

                def fail_policy(*args):
                    raise ValueError("native policy failed")

                failed_move.setattr(policy, "apply_transition", fail_policy)
                with pytest.raises(ValueError, match="native policy failed"):
                    apply_interaction_transition(*transition_args, card.last_change_seq)
            with DbSession.atomic() as db:
                rolled_back = db.exec(SqlBuilder.select.table(type(card)).where(type(card).id == card.id)).first()
                assert rolled_back.project_column_id == board[5][0].id
                assert db.exec(SqlBuilder.select.table(AppCardInteractionTransition)).all() == []
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=4) as pool:
                movements = list(
                    pool.map(lambda _: apply_interaction_transition(*transition_args, card.last_change_seq), range(4))
                )
            assert sum(result["changed"] for result in movements) == 1
            moved = movements[0]
            assert moved["state"] == "moved" and moved["execution_resumed"] is False
            assert moved["to_column_uid"] == board[5][1].get_uid()
            assert len(moves) == 1
            with TestClient(human_app) as transition_client:
                transition_path = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/interactions/{reaction_receipt['interaction_uid']}/transition"
                form = {
                    "response_uid": approved["response"]["response_uid"],
                    "expected_card_revision": card.last_change_seq,
                }
                assert transition_client.post(transition_path, json=form).status_code == 401
                assert transition_client.post(transition_path, json=form, headers=headers).status_code == 401
                access, refresh = AuthSecurity.authenticate(board[1].id)
                transition_client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                human_headers = {"Authorization": f"Bearer {access}"}
                applied = transition_client.post(transition_path, json=form, headers=human_headers)
                assert applied.status_code == 200 and applied.json()["changed"] is False
                assert (
                    transition_client.post(
                        transition_path, json={**form, "expected_card_revision": True}, headers=human_headers
                    ).status_code
                    == 400
                )
                assert (
                    transition_client.post(
                        transition_path, json={**form, "actor_uid": "A" * 11}, headers=human_headers
                    ).status_code
                    == 400
                )
            assert apply_interaction_transition(*transition_args, card.last_change_seq)["changed"] is False
            assert len(moves) == 1
            readback = asyncio.run(sdk.read(board[2].get_uid(), card.get_uid(), reaction_receipt["interaction_uid"]))
            assert readback["workflow_transitioned"] is True and readback["workflow_receipt"]["state"] == "moved"
            with DbSession.atomic() as db:
                changed_card = db.exec(SqlBuilder.select.table(type(card)).where(type(card).id == card.id)).first()
                assert changed_card.project_column_id == board[5][1].id and changed_card.order == 0
                assert changed_card.last_change_seq >= 100
                assert len(db.exec(SqlBuilder.select.table(AppCardInteractionTransition)).all()) == 1
            with DbEngine.get_main_engine().begin() as sql:
                original = transition_migration.op
                try:
                    transition_migration.op = Operations(MigrationContext.configure(sql))
                    with pytest.raises(RuntimeError, match="Cannot discard interaction transition history"):
                        transition_migration.downgrade()
                finally:
                    transition_migration.op = original
        # A pre-migration response event has no new plan field. Retain its wire body.
        from langboard_shared.domain.services.AppCardInteractions import revalidate_response_event

        with DbSession.atomic() as db:
            legacy = db.exec(
                SqlBuilder.select.table(AppCardInteraction).where(AppCardInteraction.id == reaction_args[-1])
            ).first()
            legacy.workflow_snapshot = {}
            db.update(legacy)
            legacy_event = db.exec(
                SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.id == response_event)
            ).first()
            legacy_event.payload = {key: value for key, value in legacy_event.payload.items() if key != "workflow_plan"}
            db.update(legacy_event)
            revalidate_response_event(db, legacy_event)
            binding.stage_transitions_enabled = False
            db.update(binding)
        subsequent = client.post(
            path, json={"request": {**reaction_body, "app_request_key": "followup-reaction"}}, headers=headers
        )
        assert subsequent.status_code == 200, subsequent.text
        subsequent_uid = subsequent.json()["interaction_uid"]
        with DbSession.atomic() as db:
            binding.stage_transitions_enabled = True
            db.update(binding)
            disabled_row = db.exec(
                SqlBuilder.select.table(AppCardInteraction).where(
                    AppCardInteraction.id == SnowflakeID.from_short_code(subsequent_uid)
                )
            ).first()
            assert _validate_workflow_snapshot(db, disabled_row, {"approval": {"choice_id": "approve"}}) == {
                "state": "disabled"
            }
        assert (
            accept_current_comment_reaction(native_service, board[1], board[2].id, card.id, comment.id, "thumbs-up")
            is None
        )
        assert (
            asyncio.run(sdk.cancel(board[2].get_uid(), card.get_uid(), subsequent_uid, expected_revision=1))["state"]
            == "cancelled"
        )
        from langboard_sdk import NativeApiError

        with pytest.raises(NativeApiError):
            asyncio.run(
                sdk.cancel(board[2].get_uid(), card.get_uid(), mixed_receipt["interaction_uid"], expected_revision=2)
            )
        with DbSession.atomic() as db:
            binding.granted_capabilities = [c for c in binding.granted_capabilities if c != "interactions.request"]
            db.update(binding)
        with pytest.raises(AppGovernanceDenied):
            respond_to_app_card_interaction(*args, 1, "response-1", answer)
        assert (
            client.post(
                ack_path, json={"event_uid": accepted["event_uid"], "runtime_reference": "runtime-1"}, headers=headers
            ).status_code
            == 403
        )
        with DbSession.atomic() as db:
            binding.granted_capabilities = original_grants
            db.update(binding)
            connection.state = "revoked"
            db.update(connection)
        assert client.get(read_path, headers=headers).status_code == 401
        assert client.post(path, json={"request": body}, headers=headers).status_code == 401
        assert client.post(cancel_path, json={"expected_revision": 1}, headers=headers).status_code == 401
        with pytest.raises(AppGovernanceDenied):
            respond_to_app_card_interaction(*args, 1, "response-1", answer)
        if DbEngine.get_main_engine().dialect.name == "postgresql":
            with pytest.raises(AppGovernanceDenied):
                apply_interaction_transition(*transition_args, card.last_change_seq)
        from langboard_shared.domain.services.AppCardInteractions import list_card_interactions

        retained = list_card_interactions(args[0], board[1], board[2].id, card.id)
        assert len(retained["items"]) == 6
        retained_answer = next(
            row for row in retained["items"] if row["interaction_uid"] == mixed_receipt["interaction_uid"]
        )
        assert retained_answer["delivery_state"] == "delivered" and retained_answer["reception_state"] == "received"
        from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectRole, User

        with DbSession.atomic() as db:
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.user_id == board[1].id)).first()
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == board[2].id)).first()
            project.owner_id = 2
            db.update(project)
            previous_actions = role.actions
            role.actions = []
            db.update(role)
        with pytest.raises(AppGovernanceDenied):
            list_card_interactions(args[0], board[1], board[2].id, card.id)
        with DbSession.atomic() as db:
            role.actions = previous_actions
            db.update(role)
        assert len(list_card_interactions(args[0], board[1], board[2].id, card.id)["items"]) == 6
        with pytest.raises(AppGovernanceDenied):
            list_card_interactions(args[0], board[1], 11, card.id)
        with DbSession.atomic() as db:
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == board[2].id, ProjectAssignedUser.user_id == board[1].id
                )
            ).first()
            db.delete(member)
        with pytest.raises(AppGovernanceDenied):
            list_card_interactions(args[0], board[1], board[2].id, card.id)
        with DbSession.atomic() as db:
            user = db.exec(SqlBuilder.select.table(User).where(User.id == board[1].id)).first()
            user.deleted_at = SafeDateTime.now()
            db.update(user)
        with pytest.raises(AppGovernanceDenied):
            list_card_interactions(args[0], board[1], board[2].id, card.id)
    with DbSession.atomic() as db:
        rows = db.exec(SqlBuilder.select.table(AppCardInteraction)).all()
        assert len(rows) == 6 and {row.app_request_key for row in rows} == {
            "panel-human-answer",
            "deployment-choice",
            "cancel-choice",
            "choice-or-text",
            "reaction-approval",
            "followup-reaction",
        }
        assert next(row for row in rows if row.state == "cancelled").request == cancel_body
        from langboard_shared.domain.models import AppCardInteractionResponse

        answers = db.exec(SqlBuilder.select.table(AppCardInteractionResponse)).all()
        assert len(answers) == 3
        assert next(row for row in answers if row.values == answer).actor_id == board[1].id
    with DbEngine.get_main_engine().begin() as db:
        original = response_migration.op
        try:
            response_migration.op = Operations(MigrationContext.configure(db))
            with pytest.raises(RuntimeError, match="Cannot discard human response history"):
                response_migration.downgrade()
        finally:
            response_migration.op = original
    with DbEngine.get_main_engine().begin() as db:
        original = workflow_migration.op
        try:
            workflow_migration.op = Operations(MigrationContext.configure(db))
            with pytest.raises(RuntimeError, match="Cannot discard frozen interaction workflow history"):
                workflow_migration.downgrade()
        finally:
            workflow_migration.op = original
