"""Owned attachment JSON receipts, multipart upload and current consent."""

from hashlib import sha256
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppCardAttachmentApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.domain.models import CardAttachment, CardChangeReceipt, ProjectActivity
from langboard_shared.domain.services.app_card_attachments import APP_ATTACHMENT_CAPABILITY
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.publishers import CardAttachmentPublisher, CardPublisher
from test_app_execution_grant import grant_scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_receipt_replay_hash_scope_and_deleted_attachment(board, monkeypatch, tmp_path):
    app_connection, definition, binding, _, card, _ = grant_scope(board)
    engine = DbEngine.get_main_engine()
    for model in (CardAttachment, CardChangeReceipt, ProjectActivity):
        model.__table__.create(engine, checkfirst=True)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE SEQUENCE IF NOT EXISTS content_change_seq")
    with DbSession.atomic() as db:
        definition.declaration = {
            **definition.declaration,
            "capabilities": ["resources.read", APP_ATTACHMENT_CAPABILITY],
        }
        binding.granted_capabilities = ["resources.read", APP_ATTACHMENT_CAPABILITY]
        db.update(definition)
        db.update(binding)
    token = issue_connection_credential(board[1], app_connection.id)["token"]

    def save_file(file, storage, name, original):
        (tmp_path / name).write_bytes(file.read())
        return FileModel(
            storage_type="local",
            storage_name=storage.value,
            original_filename=original,
            filename=name,
            path=f"/file/{name}",
        )

    monkeypatch.setattr(Storage, "upload_named", save_file)
    monkeypatch.setattr(CardAttachmentPublisher, "uploaded", lambda *args: None)
    monkeypatch.setattr(CardPublisher, "metadata_changed", lambda *args: None)
    app = FastAPI()
    app.include_router(AppRouter.api)
    path = f"/apps/v1/boards/{board[2].get_uid()}/cards/{card.get_uid()}/attachments/capture:1"
    headers = {"Authorization": f"Bearer {token}"}

    def send(client, data, digest=None):
        return client.post(
            path,
            headers=headers,
            files={"attachment": ("evidence.png", data)},
            data={"sha256": digest or sha256(data).hexdigest(), "byte_size": str(len(data))},
        )

    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).json() == {"found": False, "receipt": None}
        assert send(client, b"content", "0" * 64).status_code == 400
        first = send(client, b"content")
        assert first.status_code == 201, first.text
        assert first.headers["cache-control"] == "no-store"
        receipt = first.json()["receipt"]
        assert receipt["sha256"] == sha256(b"content").hexdigest()
        assert receipt["byte_size"] == 7
        assert receipt["available"] is True
        replay = send(client, b"content")
        assert replay.status_code == 200
        assert replay.json() == {"created": False, "receipt": receipt}
        assert send(client, b"changed").status_code == 409
        assert client.get(path, headers=headers).json() == {"found": True, "receipt": receipt}
        assert len(list(tmp_path.iterdir())) == 1
        with DbSession.atomic() as db:
            [attachment] = db.exec(
                SqlBuilder.select.table(CardAttachment).where(CardAttachment.card_id == card.id)
            ).all()
            assert attachment.get_uid() == receipt["attachment_uid"]
            db.delete(attachment)
        assert client.get(path, headers=headers).json()["receipt"]["available"] is False
        assert send(client, b"content").json()["receipt"]["available"] is False
        with DbSession.atomic() as db:
            binding.granted_capabilities = ["resources.read"]
            db.update(binding)
        assert client.get(path, headers=headers).status_code == 403
        assert send(client, b"content").status_code == 403
    assert len(list(tmp_path.iterdir())) == 1
