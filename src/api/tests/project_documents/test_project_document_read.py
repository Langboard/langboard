"""Real native document authorization with installed SQLite vectors and fixture inference."""

from importlib.util import module_from_spec, spec_from_file_location
from json import dumps
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.storage import FileModel, StorageName
from langboard_shared.domain.models import ProjectAssignedUser, ProjectAttachment, ProjectRole, User
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.ProjectAttachmentDocument import ProjectAttachmentDocument
from langboard_shared.domain.services import ProjectDocumentQuery as query
from langboard_shared.domain.services.factory.ProjectDocumentService import ProjectDocumentService
from langboard_shared.tasks.docling import DocumentEmbedding, DocumentVectorQuery
from langboard_shared.tasks.docling.DocumentRetrievalSettings import DocumentRetrievalSettings
from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
from langboard_shared.tasks.docling.DocumentSqliteVectorStore_test import Fixture
from langboard_shared.tasks.docling.DocumentVectorGeneration import embedding_fingerprint
from langboard_shared.tasks.docling.DocumentVectorStore import stage_vector_generation
from pydantic import ValidationError
from sqlalchemy import delete, update


_spec = spec_from_file_location(
    "document_native_fixture", Path(__file__).parents[1] / "card_cross_board/test_card_mirrors.py"
)
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
mirror_board = _module.mirror_board


@pytest.fixture
def document(mirror_board, monkeypatch, tmp_path):
    b = mirror_board
    ProjectAttachment.__table__.create(b.engine)
    ProjectAttachmentDocument.__table__.create(b.engine)
    b.documents = ProjectDocumentService(None, None, None)
    b.config = {
        "agent_llm": "OpenAI Compatible",
        "base_url": "https://fixture.invalid",
        "model_name": "fixture",
        "retrieval": {"enabled": True, "dimensions": 3, "store": "sqlite"},
    }
    snapshot = DocumentEmbedding.snapshot_embedding_config(dumps(b.config), "binding")
    b.binding = SimpleNamespace(value=dumps(b.config), bot_type=InternalBotType.DocumentEmbedding)
    b.service = SimpleNamespace(
        project_document=b.documents, internal_bot=SimpleNamespace(get_current_by_id_like=lambda _: b.binding)
    )
    with DbSession.use(readonly=False) as db:
        b.attachment = ProjectAttachment(
            project_id=b.source_board.id,
            user_id=b.actor.id,
            filename="report.pdf",
            content_sha256="a" * 64,
            file=FileModel(
                storage_type="local",
                storage_name=StorageName.ProjectAttachment.value,
                original_filename="report.pdf",
                filename="owned.pdf",
                path="/file/local/project_attachment/owned.pdf",
            ),
        )
        db.insert(b.attachment)
        b.state = ProjectAttachmentDocument(
            attachment_id=b.attachment.id,
            requested_by_id=b.actor.id,
            source_revision=b.documents.source_revision(b.attachment),
            generation="b" * 32,
            status="indexed",
            markdown="alpha 한국어 document " * 600,
            keywords={"ko": [" 키워드 ", "키워드"]},
            embedding_config=snapshot,
        )
        db.insert(b.state)
    fingerprint = embedding_fingerprint(provider=b.config["base_url"], model="fixture", dimensions=3, version="v1")
    directory = tmp_path / "document-retrieval"
    directory.mkdir()
    with open_sqlite_vector_store(directory / (fingerprint + ".sqlite"), Fixture(), dimensions=3) as store:
        pointer = stage_vector_generation(
            store,
            source={
                "source_type": "project_attachment",
                "board_uid": b.source_board.get_uid(),
                "attachment_uid": b.attachment.get_uid(),
                "content_hash": b.attachment.content_sha256,
                "embedding_fingerprint": fingerprint,
            },
            text="alpha authorized 한국어 日本語 中文",
            splitter=DocumentRetrievalSettings().splitter,
            storage={"type": "sqlite"},
        )
        store.add_texts(["alpha secret other board"], [{"board_uid": "other"}], ids=[str(uuid4())])
    with DbSession.use(readonly=False) as db:
        b.state.embedding = {
            "status": "indexed",
            "source_generation": b.state.generation,
            "pointer": pointer,
            "config": snapshot,
        }
        db.update(b.state)
    monkeypatch.setattr(query, "Env", SimpleNamespace(DATA_DIR=tmp_path, get_from_env=lambda *_: b.config["base_url"]))
    monkeypatch.setattr(DocumentEmbedding, "create_document_embeddings", lambda *_: Fixture())
    return b


def read(b, **kwargs):
    return query.read_project_document(b.service, b.actor, b.source_board.get_uid(), b.attachment.get_uid(), **kwargs)


def search(b):
    return query.search_project_document(b.service, b.actor, b.source_board.get_uid(), b.attachment.get_uid(), "alpha")


def test_real_native_read_page_and_semantic_citations(document):
    b = document
    result = read(b, offset=20, limit=60)
    assert result.text == b.state.markdown[20:80] and result.next_offset == 80
    assert result.keywords == {"ko": ["키워드"]}
    assert not {"config", "embedding_config", "pointer", "card_uid"} & result.model_dump().keys()
    found = search(b)
    assert len(found.matches) == 1 and "authorized" in found.matches[0].content
    assert found.matches[0].source.source_type == "project_attachment"
    assert "secret other board" not in found.model_dump_json()


@pytest.mark.parametrize("change", ["membership", "role", "actor", "revision", "pointer", "generation", "binding"])
def test_query_rechecks_primary_authority_source_and_binding_after_backend(document, monkeypatch, change):
    b = document
    original = DocumentVectorQuery.search_vector_generation

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        with DbSession.use(readonly=False) as db:
            if change == "membership":
                db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id))
            elif change == "role":
                db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=[]))
            elif change == "actor":
                db.exec(update(User).where(User.id == b.actor.id).values(activated_at=None))
            elif change == "revision":
                db.exec(
                    update(ProjectAttachment)
                    .where(ProjectAttachment.id == b.attachment.id)
                    .values(content_sha256="c" * 64)
                )
            elif change == "generation":
                db.exec(update(ProjectAttachmentDocument).values(generation="c" * 32))
            elif change == "pointer":
                db.exec(update(ProjectAttachmentDocument).values(embedding={}))
            else:
                b.binding.value = dumps({**b.config, "base_url": "https://replaced.invalid"})
        return result

    monkeypatch.setattr(DocumentVectorQuery, "search_vector_generation", racing)
    with pytest.raises(ValueError, match="unavailable|changed"):
        search(b)


def test_cross_board_and_changed_source_denied(document):
    b = document
    with pytest.raises(ValueError, match="unavailable"):
        query.read_project_document(b.service, b.actor, b.destination.get_uid(), b.attachment.get_uid())
    with DbSession.use(readonly=False) as db:
        db.exec(update(ProjectAttachment).values(content_sha256="c" * 64))
    with pytest.raises(ValueError, match="unavailable"):
        read(b)


def test_no_vectors_never_returns_success(document):
    with DbSession.use(readonly=False) as db:
        db.exec(update(ProjectAttachmentDocument).values(embedding={}))
    with pytest.raises(ValueError, match="vectors unavailable"):
        search(document)


def test_strict_native_transport_registration(document):
    from langboard.mcp_integration import McpTool
    from langboard.mcp_tools import ProjectDocumentsMcp
    from langboard.routes.board import BoardCloudDocumentApi as api

    for name in ("read_project_document", "search_project_document", "request_project_document_processing"):
        tool = McpTool.get_tool(name)
        assert tool and tool["accessible_type"] == "user"
        assert "user" not in tool["input_schema"]["properties"]
        assert "service" not in tool["input_schema"]["properties"]
    for values in ({"query": "x", "max_tokens": "200"}, {"query": ""}, {"query": "x", "card_uid": "invented"}):
        with pytest.raises(ValidationError):
            api.ProjectDocumentSearchInput.model_validate(values)
    b = document
    response = api.read_project_document(
        b.source_board.get_uid(), b.attachment.get_uid(), user=b.actor, service=b.service
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert (
        ProjectDocumentsMcp.read_project_document(
            b.source_board.get_uid(), b.attachment.get_uid(), b.actor, b.service
        ).source.attachment_uid
        == b.attachment.get_uid()
    )


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"offset": True}, {"limit": 8001}, {"limit": "4"}])
def test_native_bounds_before_source_access(kwargs):
    with pytest.raises(ValueError, match="bounds"):
        query.read_project_document(None, None, "board", "attachment", **kwargs)


async def test_actual_fastmcp_transport_strict_inputs_and_native_output(document):
    from functools import wraps
    from inspect import signature
    from fastmcp import Client, FastMCP
    from langboard.mcp_tools.ProjectDocumentsMcp import read_project_document

    b = document

    @wraps(read_project_document)
    def bound(**kwargs):
        return read_project_document(user=b.actor, service=b.service, **kwargs)

    sig = signature(read_project_document)
    bound.__signature__ = sig.replace(parameters=[p for n, p in sig.parameters.items() if n not in {"user", "service"}])
    server = FastMCP("Native project document transport", strict_input_validation=True)
    server.tool(bound)
    async with Client(server) as client:
        catalog = await client.list_tools()
        assert "project_attachment" in dumps(catalog[0].output_schema)
        args = {"project_uid": b.source_board.get_uid(), "attachment_uid": b.attachment.get_uid(), "limit": 40}
        result = await client.call_tool("read_project_document", args)
        assert len(result.structured_content["text"]) == 40
        for invalid in ({**args, "limit": "40"}, {**args, "limit": 8001}, {**args, "offset": -1}):
            response = await client.call_tool("read_project_document", invalid, raise_on_error=False)
            assert response.is_error


def test_pending_failed_and_new_text_keep_last_vector_source(document):
    b = document
    old_generation = b.state.generation
    text_source = b.documents._published_source(b.attachment, b.state)
    replacement = b.attachment.file.model_copy(
        update={"filename": "replacement.pdf", "path": "/file/local/project_attachment/replacement.pdf"}
    )
    with DbSession.atomic() as db:
        b.attachment.file = replacement
        b.attachment.content_sha256 = "c" * 64
        db.update(b.attachment)
        db.exec(
            update(ProjectAttachmentDocument).values(
                text_source=text_source,
                vector_source=text_source,
                generation="d" * 32,
                source_revision=b.documents.source_revision(b.attachment),
                status="pending",
            )
        )
    assert read(b).generation == old_generation
    assert read(b).text == b.state.markdown[:4000]
    found = search(b)
    assert found.generation == old_generation and found.matches
    assert found.source.url == text_source["file"]["path"]
    with DbSession.atomic() as db:
        db.exec(update(ProjectAttachmentDocument).values(status="failed"))
    assert read(b).text and search(b).matches
    with DbSession.atomic() as db:
        db.exec(
            update(ProjectAttachmentDocument).values(
                status="indexed",
                markdown="Replacement text",
                text_source={
                    **text_source,
                    "generation": "d" * 32,
                    "file": replacement.model_dump(mode="json"),
                    "content_sha256": "c" * 64,
                },
            )
        )
    assert read(b).generation == "d" * 32 and read(b).text == "Replacement text"
    assert search(b).generation == old_generation
