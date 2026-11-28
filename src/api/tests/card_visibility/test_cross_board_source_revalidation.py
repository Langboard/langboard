# ruff: noqa: F811
"""Later board inference must not preserve earlier stale source candidates."""

import importlib
import json
from hashlib import sha256
import pytest
from langboard_shared.ai.comment_compaction import CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardAttachment,
    CardComment,
    CardMetadata,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    InternalBot,
    KeywordSearchConfiguration,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.DocumentVectorCleanup import DocumentVectorCleanup
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from langchain_core.embeddings import Embeddings
from sqlalchemy import delete
from src.api.tests.model_providers.conftest import compactions  # noqa: F401
from src.api.tests.model_providers.test_comment_compaction_command import compact_command  # noqa: F401


card_module = importlib.import_module("langboard_shared.domain.services.factory.CardService")
command = importlib.import_module("langboard_shared.domain.services.factory.CardCommentCompactionService")
index = importlib.import_module("langboard_shared.tasks.docling.CompactionMetadataIndexTask")
QUERY = "availability interrupted"


@pytest.mark.parametrize("provider_result", [None, {}])
@pytest.mark.parametrize("revoke_later", [False, True])
def test_batched_keyword_failure_keeps_native_results_and_rechecks_scope(board, monkeypatch, provider_result, revoke_later):
    _, actor, project, _, _, columns, _ = board
    for model in (Bot, Card, CardAttachment, CardComment):
        model.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    later, later_column = second_board(actor)
    with DbSession.atomic() as db:
        first = Card(project_id=project.id, project_column_id=columns[0].id, title=QUERY, visibility="SHARED")
        other = Card(project_id=later.id, project_column_id=later_column.id, title=QUERY, visibility="SHARED")
        db.insert(first)
        db.insert(other)
    calls = []

    def provider(uids, query):
        calls.append((set(uids), query))
        if revoke_later:
            with DbSession.atomic() as db:
                db.exec(delete(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == later.id, ProjectAssignedUser.user_id == actor.id))
        return provider_result

    monkeypatch.setattr(card_module, "search_document_candidates_many", provider)
    monkeypatch.setattr(card_module, "search_document_candidates", lambda *_: pytest.fail("Unbatched fallback"))
    monkeypatch.setattr(command.CardCommentCompactionService, "search_metadata_candidates", lambda *_args, **_kw: {})
    result = DomainService().card.search_context_by_project(None, QUERY, user=actor, paginated=True)
    expected = {first.get_uid()} if revoke_later else {first.get_uid(), other.get_uid()}
    assert {card["uid"] for card in result["cards"]} == expected
    assert result["total_count"] == len(expected)
    assert calls == [({project.get_uid(), later.get_uid()}, QUERY)]


def second_board(actor):
    with DbSession.atomic() as db:
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=actor.id))
        db.insert(ProjectRole(project_id=project.id, user_id=actor.id, actions=["read", "card_update"]))
        column = ProjectColumn(project_id=project.id, name="Later board")
        db.insert(column)
    return project, column


@pytest.mark.parametrize("mutation", ["hash", "deleted"])
@pytest.mark.parametrize("text_match", [False, True])
def test_later_board_document_provider_invalidates_earlier_sources(board, monkeypatch, mutation, text_match):
    _, actor, project, _, _, columns, _ = board
    for model in (Bot, Card, CardAttachment, CardComment, CardMetadata):
        model.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    later, later_column = second_board(actor)
    rows, sources = [], {}
    with DbSession.atomic() as db:
        for owner, column, name in ((project, columns[0], "stale"), (project, columns[0], "earlier-valid"),
                                     (later, later_column, "later-valid")):
            card = Card(project_id=owner.id, project_column_id=column.id,
                        title=QUERY if text_match and name == "stale" else name, visibility="SHARED")
            db.insert(card)
            attachment = CardAttachment(card_id=card.id, user_id=actor.id, filename="source.pdf",
                file=FileModel(storage_type="test", storage_name="test", original_filename="source.pdf",
                    filename="source.pdf", path="/test/source.pdf"), document_text="outage evidence " + name)
            db.insert(attachment)
            rows.append((card, attachment))
            sources.setdefault(owner.get_uid(), []).append({"card_uid": card.get_uid(),
                "attachment_uid": attachment.get_uid(),
                "projection_hash": sha256(attachment.document_text.encode()).hexdigest()})
    calls = []
    def provider(uid, _query):
        calls.append(uid)
        if uid == later.get_uid():
            with DbSession.atomic() as db:
                attachment = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == rows[0][1].id)).first()
                if mutation == "hash":
                    attachment.document_text = "corrected evidence"
                else:
                    attachment.deleted_at = SafeDateTime.now()
                db.update(attachment)
        return sources[uid]
    batches = []
    def batch_provider(uids, query):
        batches.append(uids)
        return {uid: provider(uid, query) for uid in uids}
    monkeypatch.setattr(card_module, "search_document_candidates_many", batch_provider)
    monkeypatch.setattr(card_module, "search_document_candidates", lambda *_: pytest.fail("Cross-board search was not batched"))
    monkeypatch.setattr(command.CardCommentCompactionService, "search_metadata_candidates", lambda *_args, **_kw: {})
    result = DomainService().card.search_context_by_project(None, QUERY, user=actor, paginated=True)
    expected = {row[0].get_uid() for row in rows[1:]}
    if text_match:
        expected.add(rows[0][0].get_uid())
    assert {card["uid"] for card in result["cards"]} == expected
    assert result["total_count"] == len(expected)
    assert calls == [project.get_uid(), later.get_uid()]
    assert batches == [[project.get_uid(), later.get_uid()]]
    stale = next((card for card in result["cards"] if card["uid"] == rows[0][0].get_uid()), {})
    assert "document_matches" not in stale
    assert all(card.get("document_matches") for card in result["cards"] if card["uid"] != rows[0][0].get_uid())


class Provider(Embeddings):
    def __init__(self):
        self.query_calls = 0
        self.hook = None

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.01] for _ in texts]

    def embed_query(self, text):
        self.query_calls += 1
        if self.hook:
            self.hook(self.query_calls)
        return [1.0, 0.0, 0.01]


@pytest.mark.parametrize("text_match", [False, True])
async def test_later_board_inference_invalidates_earlier_generation(compact_command, monkeypatch, tmp_path, text_match):
    service, actor, project, stale, *_ = compact_command
    for model in (DocumentEmbeddingCache, DocumentEmbeddingCacheReference, DocumentVectorCleanup,
                  CardAttachment, KeywordSearchConfiguration):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    later, later_column = second_board(actor)
    provider = Provider()
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path))
    monkeypatch.setattr(index, "create_document_embeddings", lambda *_: provider)
    monkeypatch.setattr(index, "enqueue_index", lambda *_: False)
    monkeypatch.setattr(card_module, "search_document_candidates_many", lambda *_: None)
    async def compact(_binding, snapshot):
        refs = [source.evidence_ref for source in snapshot.sources]
        return CompactionDraft(claims=[{"text": "Outage", "kind": "unresolved", "evidence_refs": refs}],
                               keywords=[{"value": "outage", "evidence_refs": refs}])
    monkeypatch.setattr(command, "request_compaction_draft", compact)
    with DbSession.atomic() as db:
        binding = InternalBot(bot_type=InternalBotType.DocumentEmbedding, is_default=True,
            platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
            display_name="Native embedding", value=json.dumps({"agent_llm": "OpenAI", "model_name": "fixture",
                "base_url": "http://embedding.invalid/v1", "retrieval": {"enabled": True, "store": "sqlite",
                    "dimensions": 3, "score_threshold": 0.9}}))
        db.insert(binding)
        stale.title = QUERY if text_match else "stale"
        db.update(stale)
        earlier = Card(project_id=project.id, project_column_id=stale.project_column_id, title="earlier-valid")
        last = Card(project_id=later.id, project_column_id=later_column.id, title="later-valid")
        for card in (earlier, last):
            db.insert(card)
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="outage")))
    native = DomainService()
    metadata_ids = []
    for owner, card in ((project, stale), (project, earlier), (later, last)):
        assert (await service.generate(actor, owner, card))["status"] == "completed"
        with DbSession.use(readonly=False) as db:
            row = db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.card_id == card.id)).first()
        metadata_ids.append(row.id)
        assert await index.index_metadata(native, row.id)
    from langboard_shared.tasks.docling.DocumentLangGraphVectorStore import DocumentLangGraphVectorStore

    original_search = DocumentLangGraphVectorStore.similarity_search_with_score
    store_calls = []
    def invalidate(store, *args, **kwargs):
        hits = original_search(store, *args, **kwargs)
        store_calls.append(kwargs)
        if len(store_calls) == 2:
            with DbSession.atomic() as db:
                row = index._row(db, metadata_ids[0])
                data = json.loads(row.value)
                data["index"]["pointer"]["generation"] = "replaced-after-earlier-board-query"
                assert index._cas(db, row, data)
        return hits
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", invalidate)
    result = native.card.search_context_by_project(None, QUERY, user=actor, paginated=True)
    expected = {earlier.get_uid(), last.get_uid()}
    if text_match:
        expected.add(stale.get_uid())
    assert {card["uid"] for card in result["cards"]} == expected
    assert result["total_count"] == len(expected)
    assert provider.query_calls == 1
    assert len(store_calls) == 2
    stale_result = next((card for card in result["cards"] if card["uid"] == stale.get_uid()), {})
    assert "search_metadata" not in stale_result
    assert all(card.get("search_metadata", {}).get("similarity") for card in result["cards"] if card["uid"] != stale.get_uid())
