"""Real storage/DB/native auth flow with an absent optional converter and captured delivery."""

import importlib
from importlib.util import find_spec
from json import dumps
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board import BoardCardAttachmentApi
from langboard_shared.core.broker import Broker
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.storage import Storage
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    CardMetadata,
    InternalBot,
    KeywordSearchConfiguration,
    ProjectActivity,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.Env import Env
from langboard_shared.publishers import CardAttachmentPublisher, CardPublisher
from langboard_shared.tasks.activities import CardAttachmentActivityTask
from langboard_shared.tasks.bots import CardAttachmentBotTask
from langboard_shared.tasks.docling import DoclingMetadataTask


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_optional_converter_absence_preserves_http_upload_and_reports_worker_failure(board, monkeypatch, tmp_path):  # noqa: F811
    if find_spec("docling") is not None:
        pytest.skip("This integration gate requires an environment without document-processing installed")
    _, actor, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    for model in (Card, CardAttachment, CardMetadata, InternalBot, KeywordSearchConfiguration, ProjectActivity):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    with DbSession.use(readonly=False) as db:
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Optional conversion")
        db.insert(card)
        binding = InternalBot(
            bot_type=InternalBotType.DocumentVision,
            display_name="Fixture vision",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
            is_default=True,
            value=dumps(
                {"base_url": "https://provider.invalid/v1", "model_name": "vision", "document_processing_enabled": True}
            ),
        )
        db.insert(binding)
    # Exercise the native member policy without provisioning an employee directory in this converter fixture.
    monkeypatch.setenv("CARD_INTERNAL_ACCESS_MODE", "project_members")
    service = DomainService()
    if engine.dialect.name == "sqlite":
        from itertools import count

        sequence = count(1)
        with engine.connect() as connection:
            connection.connection.driver_connection.create_function("nextval", 1, lambda _: next(sequence))
    else:
        from sqlalchemy import text

        with engine.begin() as db:
            db.execute(text("CREATE SEQUENCE content_change_seq"))
    monkeypatch.setattr(CardAttachmentPublisher, "uploaded", Mock())
    monkeypatch.setattr(CardPublisher, "metadata_changed", Mock())
    monkeypatch.setattr(CardAttachmentActivityTask, "card_attachment_uploaded", Mock())
    monkeypatch.setattr(
        importlib.import_module("langboard_shared.domain.services.CardAttachmentUploadOperations"),
        "record_project_activity", Mock(),
    )
    monkeypatch.setattr(CardAttachmentBotTask, "card_attachment_uploaded", Mock())
    monkeypatch.setattr(DoclingMetadataService, "publish_update", Mock())
    # Card visibility has dedicated tests; this gate isolates upload/conversion, not its ACL proof.
    monkeypatch.setattr(BoardCardAttachmentApi, "require_visible_card", lambda *args: card)
    delivered = []

    def send_task(name, *, args, kwargs):
        with DbSession.use(readonly=False) as db:
            assert db.exec(SqlBuilder.select.table(CardAttachment)).first() is not None
        delivered.append((name, args, kwargs))

    monkeypatch.setattr(Broker.celery, "send_task", send_task)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is BoardCardAttachmentApi.upload_card_attachment:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    path = f"/board/{project.get_uid()}/card/{card.get_uid()}/attachment"
    payload = b"%PDF-1.4 optional-converter-fixture"
    with TestClient(app) as client:
        assert client.post(path, files={"attachment": ("report.pdf", payload)}).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.post(
            path, files={"attachment": ("report.pdf", payload)}, headers={"Authorization": f"Bearer {access}"}
        )
        assert response.status_code == 201, response.text
    with DbSession.use(readonly=False) as db:
        attachment = db.exec(SqlBuilder.select.table(CardAttachment)).first()
    assert attachment is not None and Storage.get_file(attachment.file) == payload
    assert len(delivered) == 1 and delivered[0][0].endswith("index_card_attachment")
    document = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert document["status"] == "pending"
    download = Mock(side_effect=AssertionError("Missing optional converter must fail before file IO"))
    monkeypatch.setattr(DoclingMetadataTask.Storage, "download_file", download)
    DoclingMetadataTask._index_card_attachment(service, attachment, generation=document["generation"])
    failed = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert failed["status"] == "failed"
    assert "document-processing extra" in failed["error_message"]
    download.assert_not_called()
    assert Storage.get_file(attachment.file) == payload
    assert len(delivered) == 1


@pytest.fixture
def keyword_http(board, monkeypatch):
    """Native JWT, role and visibility checks with durable per-source requests."""
    from langboard_shared.core.storage import FileModel
    from langboard_shared.domain.models import (
        CardAppOwnership,
        DocumentKeywordIndex,
        EmployeeMembershipPolicy,
        KeywordSearchConfiguration,
        KeywordSearchConnection,
        ProjectRole,
        UserIdentityLink,
    )
    from langboard_shared.tasks.docling import DocumentKeywordTask
    from sqlalchemy import update

    _, actor, project, _, role, columns, _ = board
    engine = DbEngine.get_main_engine()
    for model in (
        Card,
        CardAttachment,
        CardMetadata,
        DocumentKeywordIndex,
        KeywordSearchConfiguration,
        KeywordSearchConnection,
        EmployeeMembershipPolicy,
        CardAppOwnership,
        UserIdentityLink,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: "https://fixture.invalid"))
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "true")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", "https://keyword.invalid")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX", "attachment-keywords")
    monkeypatch.setenv("LANGBOARD_ELASTICSEARCH_API_KEY", "test-only")
    with DbSession.use(readonly=False) as db:
        db.insert(EmployeeMembershipPolicy(key="global", issuer="https://fixture.invalid", group_uids=[]))
        db.exec(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=["read", "card_update"]))
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Keyword source", visibility="SHARED")
        other = Card(project_id=project.id, project_column_id=columns[0].id, title="Other source", visibility="SHARED")
        db.insert(card)
        db.insert(other)
        attachment = CardAttachment(
            card_id=card.id,
            user_id=actor.id,
            filename="report.pdf",
            document_text="Current complete text",
            file=FileModel(
                storage_type="local",
                storage_name="fixture",
                original_filename="report.pdf",
                filename="report.pdf",
                path="/never/read",
            ),
        )
        db.insert(attachment)
    service = DomainService()
    document = {
        "attachment_uid": attachment.get_uid(),
        "status": "indexed",
        "generation": "current-generation",
        "content_hash": "source-hash",
        "content": {"markdown": "Current complete text"},
    }
    service.docling_metadata.upsert_document(CardMetadata, card, document)
    delivered = []
    monkeypatch.setattr(Broker.celery, "send_task", lambda name, **kwargs: delivered.append((name, kwargs)))
    monkeypatch.setattr(
        Storage, "get_file", Mock(side_effect=AssertionError("Keyword reindex must not read source files"))
    )
    monkeypatch.setattr(
        Storage, "download_file", Mock(side_effect=AssertionError("Keyword reindex must not download source files"))
    )
    monkeypatch.setattr(
        service.card_attachment,
        "request_document_processing",
        Mock(side_effect=AssertionError("Keyword reindex must not run VLM")),
    )
    monkeypatch.setattr(
        service.card_attachment,
        "request_document_embedding",
        Mock(side_effect=AssertionError("Keyword reindex must not run embeddings")),
    )
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is BoardCardAttachmentApi.process_card_attachment_document:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        client.headers["Authorization"] = f"Bearer {access}"
        path = f"/board/{project.get_uid()}/card/{card.get_uid()}/attachment/{attachment.get_uid()}/document-processing"
        yield (
            client,
            path,
            service,
            actor,
            project,
            role,
            card,
            other,
            attachment,
            document,
            delivered,
            DocumentKeywordTask,
        )
    service.close()


def keyword_jobs():
    from langboard_shared.domain.models import DocumentKeywordIndex

    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(DocumentKeywordIndex)).all()


def test_keyword_http_current_text_and_broker_recovery(keyword_http, monkeypatch):
    client, path, service, actor, project, _, card, _, attachment, document, delivered, keyword = keyword_http
    assert client.post(path, json={"mode": "keyword"}).json() == {"status": "pending"}
    job = keyword_jobs()[0]
    assert job.source_generation == document["generation"] and job.due_at is not None
    assert len(delivered) == 1 and delivered[0][0] == keyword.TASK_NAME
    before = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert before == document
    sent = []
    monkeypatch.setattr(keyword, "_send", lambda job, payload: sent.append(payload))
    assert keyword.process_keyword(int(job.id))
    assert sent[0]["text"] == "Current complete text" and sent[0]["active"] is True
    monkeypatch.setattr(Broker.celery, "send_task", Mock(side_effect=RuntimeError("broker unavailable")))
    assert client.post(path, json={"mode": "keyword"}).status_code == 200
    retained = keyword_jobs()[0]
    assert retained.id == job.id and retained.version > job.version and retained.due_at is not None
    recovered = []
    monkeypatch.setattr(Broker.celery, "send_task", lambda name, **kwargs: recovered.append(name))
    assert keyword.recover_keyword() == 1 and recovered == [keyword.TASK_NAME]
    assert keyword.process_keyword(int(job.id))
    assert keyword_jobs()[0].due_at is None
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            assert (
                service.card_attachment.request_document_keyword_index(project, card, attachment, user=actor)
                == "pending"
            )
            raise RuntimeError("rollback")
    assert keyword_jobs()[0].version == retained.version
    assert len(recovered) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "read-only",
        "internal",
        "private",
        "wrong-board",
        "wrong-card",
        "deleted",
        "deleted-card",
        "deleted-board",
        "stale",
        "hash",
        "generation",
        "text",
        "oversized",
        "disabled",
        "provider",
        "destination",
        "ca",
        "timeout",
    ],
)
def test_keyword_http_rejects_unavailable_sources_and_authority(keyword_http, monkeypatch, failure):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Project, ProjectRole
    from sqlalchemy import update

    client, path, service, _, project, role, card, other, attachment, document, delivered, _ = keyword_http
    expected = 404
    with DbSession.use(readonly=False) as db:
        if failure == "read-only":
            db.exec(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=["read"]))
            expected = 403
        elif failure in ("internal", "private"):
            db.exec(
                update(Card)
                .where(Card.id == card.id)
                .values(
                    visibility=failure.upper(),
                    owner_user_id=2 if failure == "private" else None,
                    created_by_user_id=2 if failure == "private" else None,
                )
            )
        elif failure == "wrong-board":
            db.exec(update(Card).where(Card.id == card.id).values(project_id=11))
        elif failure == "wrong-card":
            db.exec(update(CardAttachment).where(CardAttachment.id == attachment.id).values(card_id=other.id))
        elif failure == "deleted":
            db.exec(
                update(CardAttachment).where(CardAttachment.id == attachment.id).values(deleted_at=SafeDateTime.now())
            )
        elif failure == "deleted-card":
            db.exec(update(Card).where(Card.id == card.id).values(deleted_at=SafeDateTime.now()))
        elif failure == "deleted-board":
            db.exec(update(Project).where(Project.id == project.id).values(deleted_at=SafeDateTime.now()))
            expected = 403
        elif failure in ("text", "oversized"):
            db.exec(
                update(CardAttachment)
                .where(CardAttachment.id == attachment.id)
                .values(document_text="" if failure == "text" else "x" * 131073)
            )
            expected = 400
    if failure in ("stale", "hash", "generation"):
        document[{"stale": "status", "hash": "content_hash", "generation": "generation"}[failure]] = (
            "processing" if failure == "stale" else ""
        )
        service.docling_metadata.upsert_document(CardMetadata, card, document)
        expected = 400
    if failure in ("disabled", "provider", "destination", "ca", "timeout"):
        variable = {
            "disabled": "DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED",
            "provider": "LANGBOARD_ELASTICSEARCH_API_KEY",
            "destination": "DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT",
            "ca": "DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS",
            "timeout": "DOCUMENT_KEYWORD_ELASTICSEARCH_WRITE_TIMEOUT",
        }[failure]
        monkeypatch.setenv(
            variable,
            "false"
            if failure == "disabled"
            else "/missing/keyword-ca.pem"
            if failure == "ca"
            else "invalid"
            if failure == "timeout"
            else "",
        )
        expected = 400
    response = client.post(path, json={"mode": "keyword"})
    assert response.status_code == expected, response.text
    assert keyword_jobs() == [] and delivered == []


def test_keyword_http_requires_native_auth(keyword_http):
    client, path, *rest = keyword_http
    client.cookies.clear()
    client.headers.pop("Authorization")
    assert client.post(path, json={"mode": "keyword"}).status_code == 401
    assert keyword_jobs() == []


@pytest.mark.parametrize("stale_field", ["generation", "content_hash"])
def test_keyword_worker_fences_changed_generation_and_deletion(keyword_http, monkeypatch, stale_field):
    from langboard_shared.core.types import SafeDateTime
    from sqlalchemy import update

    client, path, service, _, _, _, card, _, attachment, document, _, keyword = keyword_http
    assert client.post(path, json={"mode": "keyword"}).status_code == 200
    job = keyword_jobs()[0]
    sent = []
    monkeypatch.setattr(keyword, "_send", lambda job, payload: sent.append(payload))
    document[stale_field] = "replacement" if stale_field == "generation" else ""
    service.docling_metadata.upsert_document(CardMetadata, card, document)
    assert not keyword.process_keyword(int(job.id))
    assert sent == [] and keyword_jobs()[0].last_error == "source_superseded"
    document["content_hash"] = "replacement-hash"
    service.docling_metadata.upsert_document(CardMetadata, card, document)
    assert client.post(path, json={"mode": "keyword"}).status_code == 200
    with DbSession.use(readonly=False) as db:
        db.exec(update(CardAttachment).where(CardAttachment.id == attachment.id).values(deleted_at=SafeDateTime.now()))
    assert not keyword.process_keyword(int(job.id))
    assert keyword_jobs()[0].operation == "remove"
    assert keyword.process_keyword(int(job.id))
    assert len(sent) == 1 and sent[0]["active"] is False


@pytest.mark.parametrize("change", ["visibility", "attachment"])
def test_keyword_http_rechecks_source_after_route_validation(keyword_http, monkeypatch, change):
    from langboard_shared.core.types import SafeDateTime
    from sqlalchemy import update

    client, path, _, _, _, _, card, _, attachment, _, delivered, _ = keyword_http
    original = BoardCardAttachmentApi.require_card_child

    def validate_then_change(*args):
        original(*args)
        with DbSession.use(readonly=False) as db:
            if change == "visibility":
                db.exec(update(Card).where(Card.id == card.id).values(visibility="INTERNAL"))
            else:
                db.exec(
                    update(CardAttachment)
                    .where(CardAttachment.id == attachment.id)
                    .values(deleted_at=SafeDateTime.now())
                )

    monkeypatch.setattr(BoardCardAttachmentApi, "require_card_child", validate_then_change)
    assert client.post(path, json={"mode": "keyword"}).status_code == 404
    assert keyword_jobs() == [] and delivered == []


def test_keyword_request_preserves_native_app_mutation_guard(keyword_http):
    from langboard_shared.domain.models import Bot, CardAppOwnership
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied

    _, _, service, _, project, _, card, _, attachment, _, delivered, _ = keyword_http
    with DbSession.use(readonly=False) as db:
        db.insert(CardAppOwnership(card_id=card.id, app_key="fixture-owned"))
    with pytest.raises(AppGovernanceDenied):
        service.card_attachment.request_document_keyword_index(
            project, card, attachment, user=Bot.model_construct(id=99)
        )
    assert keyword_jobs() == [] and delivered == []


def test_keyword_request_rollback_does_not_capture_or_dispatch(keyword_http):
    _, _, service, actor, project, _, card, _, attachment, _, delivered, _ = keyword_http
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            assert (
                service.card_attachment.request_document_keyword_index(project, card, attachment, user=actor)
                == "pending"
            )
            raise RuntimeError("rollback")
    assert keyword_jobs() == [] and delivered == []
