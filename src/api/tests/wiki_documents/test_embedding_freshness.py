"""Native metadata publication and authorized read projection preserve frozen vectors."""

from copy import deepcopy
from json import dumps, loads
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.storage import FileModel
from langboard_shared.domain.models import Card, CardAttachment, CardMetadata, InternalBot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.DoclingMetadataService import DOCLING_DOCUMENTS_METADATA_KEY as KEY
from langboard_shared.tasks.docling.DocumentEmbedding import snapshot_embedding_config
from langboard_shared.tasks.docling.DocumentVectorGeneration import embedding_fingerprint
from sqlalchemy import event, update


@pytest.fixture
def generation(mirror_board):
    b = mirror_board
    for model in (CardAttachment, CardMetadata, InternalBot):
        model.__table__.create(b.engine)
    b.config = dict(
        agent_llm="OpenAI Compatible",
        base_url="https://private.invalid/v1",
        model_name="old",
        api_key="secret",
        retrieval=dict(enabled=True, dimensions=3, store="sqlite"),
    )
    with DbSession.use(readonly=False) as db:
        b.binding = InternalBot(
            bot_type=InternalBotType.DocumentEmbedding,
            is_default=True,
            display_name="Fixture",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
            value=dumps(b.config),
        )
        db.insert(b.binding)
        b.attachment = CardAttachment(
            user_id=b.actor.id,
            card_id=b.source.id,
            filename="fixture.md",
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="fixture.md",
                original_filename="fixture.md",
                path="/fixture.md",
            ),
        )
        db.insert(b.attachment)
    b.domain = DomainService()
    b.snapshot = snapshot_embedding_config(dumps(b.config), b.binding.get_uid())
    b.embedding = dict(
        status="indexed",
        config=b.snapshot,
        chunk_version="v1",
        source_generation="generation",
        pointer={
            "board_uid": b.source_board.get_uid(),
            "card_uid": b.source.get_uid(),
            "attachment_uid": b.attachment.get_uid(),
            "content_hash": "hash",
            "embedding_fingerprint": embedding_fingerprint(
                provider=b.config["base_url"], model="old", dimensions=3, version="v1"
            )
        },
    )
    b.domain.docling_metadata.upsert_document(
        CardMetadata,
        b.source,
        dict(
            attachment_uid=b.attachment.get_uid(),
            status="indexed",
            generation="generation",
            content_hash="hash",
            content={"markdown": "original"},
            embedding_config=b.snapshot,
        ),
    )
    assert b.domain.docling_metadata.publish_document_embedding(
        b.source, b.attachment.get_uid(), "generation", "hash", b.embedding
    )
    b.raw = b.domain.docling_metadata.load_documents(CardMetadata, b.source)
    return b


def read(b):
    value = b.domain.metadata.get_all_as_api(CardMetadata, b.source, as_dict=True)[KEY]
    assert "secret" not in value and "private.invalid" not in value
    projected = loads(value)[0]["embedding"]["freshness"]
    keyed = b.domain.metadata.get_by_key_as_api(CardMetadata, b.source, KEY)
    listed = b.domain.metadata.get_all_as_api(CardMetadata, b.source)
    assert loads(keyed["value"])[0]["embedding"]["freshness"] == projected
    assert loads(next(row["value"] for row in listed if row["key"] == KEY))[0]["embedding"]["freshness"] == projected
    return projected


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"model_name": "new"}, "model"),
        ({"agent_llm": "LiteLLM"}, "provider"),
        ({"base_url": "https://other.invalid"}, "provider"),
        ({"retrieval": {"dimensions": 4}}, "dimensions"),
        ({"retrieval": {"splitter": {"chunk_size": 1200}}}, "splitter"),
        ({"retrieval": {"store": "postgres"}}, "storage"),
    ],
)
def test_native_publication_read_detects_changed_generation_without_mutation(generation, change, reason):
    b = generation
    assert read(b) == {"status": "current", "reasons": []}
    config = deepcopy(b.config)
    config.update({k: v for k, v in change.items() if k != "retrieval"})
    config["retrieval"].update(change.get("retrieval", {}))
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBot).where(InternalBot.id == b.binding.id).values(value=dumps(config)))
    assert read(b) == {"status": "outdated", "reasons": [reason]}
    assert b.domain.docling_metadata.load_documents(CardMetadata, b.source) == b.raw
    assert b.domain.metadata.get_by_key_as_api(CardMetadata, b.source, KEY, internal=True)["value"] == dumps(
        b.raw, ensure_ascii=False, separators=(",", ":")
    )


def test_credentials_query_tuning_unavailable_and_legacy(generation):
    b = generation
    config = deepcopy(b.config)
    config["api_key"] = "rotated-secret"
    config["retrieval"].update(k=10, fetch_k=30, max_return_tokens=8000, timeout_seconds=3, score_threshold=0.4)
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBot).where(InternalBot.id == b.binding.id).values(value=dumps(config)))
    assert read(b)["status"] == "current"
    config["retrieval"]["enabled"] = False
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBot).where(InternalBot.id == b.binding.id).values(value=dumps(config)))
    assert read(b)["status"] == "unavailable"
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBot).where(InternalBot.id == b.binding.id).values(is_default=False))
    assert read(b)["status"] == "unavailable"
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(InternalBot).where(InternalBot.id == b.binding.id).values(is_default=True, value=dumps(b.config))
        )
    document = deepcopy(b.raw[0])
    del document["embedding"]["config"]
    b.domain.docling_metadata.upsert_document(CardMetadata, b.source, document)
    assert read(b)["status"] == "unknown"
    document["embedding"] = {**b.embedding, "chunk_version": "old"}
    b.domain.docling_metadata.upsert_document(CardMetadata, b.source, document)
    assert read(b) == {"status": "outdated", "reasons": ["index_schema"]}


def test_batch_comparison_once_preserves_private_linked_and_internal_boundaries(generation, monkeypatch):
    b = generation
    with DbSession.use(readonly=False) as db:
        private = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Private",
            visibility="PRIVATE",
            owner_user_id=b.outsider.id,
            created_by_user_id=b.outsider.id,
        )
        linked = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Linked",
            source_type="wiki",
            source_uid="wiki",
        )
        shared = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Shared",
            visibility="SHARED",
        )
        db.insert(shared)
        db.insert(private)
        db.insert(linked)
    for card in (private, linked, shared):
        b.domain.docling_metadata.save_documents(CardMetadata, card, b.raw)
    b.domain.metadata.save(CardMetadata, b.source, "internal.work_plan.receipt", "receipt", internal=True)
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=b.actor.id)
    monkeypatch.setattr(b.domain.card, "resolve_visibility_context", lambda *args: (b.source_board, context))
    queries = []

    def capture(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith("SELECT") and "internal_bot" in statement:
            queries.append(statement)

    event.listen(b.engine, "before_cursor_execute", capture)
    try:
        rows = b.domain.metadata.get_project_cards_as_api(b.source_board, b.actor, CollaborationChannel.HumanUI)
    finally:
        event.remove(b.engine, "before_cursor_execute", capture)
    assert len(queries) == 1
    assert set(rows) == {b.source.get_uid(), shared.get_uid()}
    assert "internal.work_plan.receipt" not in rows[b.source.get_uid()]
    assert loads(rows[b.source.get_uid()][KEY])[0]["embedding"]["freshness"]["status"] == "current"
    assert b.domain.metadata.get_all_as_api(CardMetadata, linked, as_dict=True) == {}
    assert b.domain.metadata.get_by_key_as_api(CardMetadata, linked, KEY) is None
    monkeypatch.setattr(b.domain.card, "resolve_visibility_context", lambda *args: None)
    assert b.domain.metadata.get_project_cards_as_api(b.source_board, b.actor) == {}
