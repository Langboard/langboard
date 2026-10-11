# ruff: noqa: F811
"""Native app request persistence, dedupe, and revocation without runtime effects."""

import importlib
from datetime import timedelta
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppCardInteractionApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import AppCardInteraction, AppDefinition, BoardAppBinding
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_execution_ack import scope


@pytest.mark.parametrize("board", ["sqlite-http"], indirect=True)
def test_native_app_owned_choice_creation_read_dedupe_and_denial(board, monkeypatch):
    connection, card, token, _ = scope(board, monkeypatch)
    migration = importlib.import_module("langboard.migrations.versions.20261011090000-ed71a806b2f4")
    original = migration.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            migration.op = Operations(MigrationContext.configure(db))
            migration.upgrade()
    finally:
        migration.op = original
    with DbSession.atomic() as db:
        definition = db.exec(
            SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key)
        ).first()
        definition.declaration = {
            **definition.declaration,
            "capabilities": [*definition.declaration["capabilities"], "interactions.request"],
        }
        db.update(definition)
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == connection.app_key)
        ).first()
        binding.granted_capabilities = [*binding.granted_capabilities, "interactions.request"]
        db.update(binding)
    from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential

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
        second = client.post(path, json={"request": body}, headers=headers).json()
        assert second["changed"] is False and second["interaction_uid"] == receipt["interaction_uid"]
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
        saved = asyncio.run(sdk.create(board[2].get_uid(), card.get_uid(), body))
        assert saved["changed"] is False
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
        assert client.post(path, json={"request": body}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            binding.granted_capabilities = [*binding.granted_capabilities, "interactions.request"]
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
        with DbSession.atomic() as db:
            connection.state = "revoked"
            db.update(connection)
        assert client.get(read_path, headers=headers).status_code == 401
        assert client.post(path, json={"request": body}, headers=headers).status_code == 401
        assert client.post(cancel_path, json={"expected_revision": 1}, headers=headers).status_code == 401
    with DbSession.atomic() as db:
        rows = db.exec(SqlBuilder.select.table(AppCardInteraction)).all()
        assert len(rows) == 3 and {row.app_request_key for row in rows} == {
            "deployment-choice",
            "cancel-choice",
            "choice-or-text",
        }
        assert next(row for row in rows if row.state == "cancelled").request == cancel_body
