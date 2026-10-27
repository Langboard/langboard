"""Native store retrieval and durable compaction metadata lifecycle fences."""

import importlib
import json
import pytest
from langboard_shared.ai.comment_compaction import CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    CardComment,
    CardMetadata,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    InternalBot,
    KeywordSearchConfiguration,
    ProjectRole,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.DocumentVectorCleanup import DocumentVectorCleanup
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from langboard_shared.tasks.docling.DocumentEmbeddingTask import remove_recorded_embedding
from langchain_core.embeddings import Embeddings
from test_comment_compaction_command import automatic_command, command, compact_command  # noqa: F401
from tests.model_providers.workflow_rule_fixtures import rule_id


index = importlib.import_module("langboard_shared.tasks.docling.CompactionMetadataIndexTask")


class SemanticProvider(Embeddings):
    """A deterministic provider boundary; native SQLite owns cosine retrieval."""
    def __init__(self):
        self.document_calls = 0
        self.query_calls = 0
        self.document_hook = None
        self.query_hook = None

    def embed_documents(self, texts):
        assert not DbSession.has_active_transaction()
        if texts == ["availability interrupted"]:
            if self.query_hook:
                hook, self.query_hook = self.query_hook, None
                hook()
        else:
            self.document_calls += 1
            if self.document_hook:
                hook, self.document_hook = self.document_hook, None
                hook()
        return [self.vector(text) for text in texts]

    async def aembed_documents(self, texts):
        return self.embed_documents(texts)

    def embed_query(self, text):
        self.query_calls += 1
        assert not DbSession.has_active_transaction()
        if self.query_hook:
            hook, self.query_hook = self.query_hook, None
            hook()
        return self.vector(text)

    @staticmethod
    def vector(text):
        return [1.0, 0.0, 0.01] if "outage" in text.lower() or "availability interrupted" in text.lower() else [0.0, 1.0, 0.01]


def metadata(card):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.card_id == card.id)
                       .order_by(CardMetadata.id.desc()).execution_options(populate_existing=True)).first()


@pytest.fixture
def semantic(compact_command, monkeypatch, tmp_path):  # noqa: F811
    service, actor, project, card, _, comment, *_ = compact_command
    for model in (DocumentEmbeddingCache, DocumentEmbeddingCacheReference, DocumentVectorCleanup, CardAttachment, KeywordSearchConfiguration):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path))
    provider = SemanticProvider()
    monkeypatch.setattr(index, "create_document_embeddings", lambda *_: provider)
    monkeypatch.setattr(index, "enqueue_index", lambda *_: False)
    with DbSession.atomic() as db:
        binding = InternalBot(bot_type=InternalBotType.DocumentEmbedding, is_default=True,
            platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
            display_name="Native embedding", value=json.dumps({"agent_llm": "OpenAI", "model_name": "fixture",
                "base_url": "http://embedding.invalid/v1", "retrieval": {"enabled": True, "store": "sqlite",
                    "dimensions": 3, "score_threshold": 0.9}}))
        db.insert(binding)
        comment.content = EditorContentModel(content="outage")
        db.update(comment)
    async def compact(_, snapshot):
        keyword = "outage" if any("outage" in source.text for source in snapshot.sources) else "weather"
        refs = [source.evidence_ref for source in snapshot.sources if keyword in source.text]
        return CompactionDraft(claims=[{"text": "Reported availability issue", "kind": "unresolved", "evidence_refs": refs}],
            keywords=[{"value": keyword, "evidence_refs": refs}])
    monkeypatch.setattr(command, "request_compaction_draft", compact)
    return service, actor, project, card, comment, binding, provider


async def generate(semantic):
    service, actor, project, card, *_ = semantic
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed"
    return metadata(card)


async def test_native_semantic_retrieval_without_overlap_reaches_card_search_and_mcp(semantic):
    service, actor, project, card, comment, _, provider = semantic
    row = await generate(semantic)
    assert json.loads(row.value)["index"] == {"status": "pending"}
    native = DomainService()
    assert await index.index_metadata(native, row.id)
    assert provider.document_calls == 1
    results = native.card.search_context_by_project(project, "availability interrupted", user=actor)
    assert [item["uid"] for item in results] == [card.get_uid()]
    hit = results[0]["search_metadata"]["similarity"][0]
    assert hit["similarity_score"] > 0.99
    assert hit["source"]["source_type"] == "comment_compaction"
    assert "attachment_uid" not in hit["source"]
    assert f"comment:{comment.get_uid()}" in hit["evidence_refs"]
    assert "availability interrupted" not in hit["content"]
    from langboard.mcp_tools.CardMcp import search_card_compaction_metadata
    mcp = search_card_compaction_metadata(project.get_uid(), card.get_uid(), "availability interrupted", actor, native)
    assert mcp["matches"][0]["compaction_uid"] == hit["compaction_uid"]
    assert mcp["matches"][0]["source_revision"] == hit["source_revision"]
    assert not any(key in str(mcp) for key in ("api_key", "binding_uid", "cache_use_token"))
    assert await index.index_metadata(native, row.id)
    assert provider.document_calls == 1


@pytest.mark.parametrize("change", ["edit", "delete", "provider", "permission"])
async def test_inference_revalidates_every_mutable_authority(semantic, change):
    service, actor, project, card, comment, binding, provider = semantic
    row = await generate(semantic)
    def revoke():
        with DbSession.atomic() as db:
            if change == "edit":
                comment.content = EditorContentModel(content="corrected")
                db.update(comment)
            elif change == "delete":
                from langboard_shared.core.types import SafeDateTime
                comment.deleted_at = SafeDateTime.now()
                db.update(comment)
            elif change == "provider":
                binding.is_default = False
                db.update(binding)
            else:
                role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.project_id == project.id,
                    ProjectRole.user_id == actor.id)).first()
                role.actions = []
                db.update(role)
    provider.document_hook = revoke
    assert await index.index_metadata(DomainService(), row.id) is False
    assert json.loads(metadata(card).value)["index"]["status"] == "retired"
    with DbSession.use(readonly=False) as db:
        cleanup = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()
    assert cleanup and all(event.pointer["source_type"] == "comment_compaction" for event in cleanup)
    assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                 channel=CollaborationChannel.Api) == []


async def test_manual_compactor_change_is_serialized_with_pointer_publication(semantic, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import text, update
    from sqlalchemy.exc import OperationalError

    engine = DbEngine.get_main_engine()
    if engine.dialect.name != "postgresql":
        pytest.skip("Concurrent row-lock proof requires PostgreSQL")
    _, actor, project, card, *_ = semantic
    row = await generate(semantic)
    compactor = DomainService().internal_bot.get_comment_compaction_binding()
    outcomes = []
    original = index._cas

    def change_binding():
        try:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
                connection.execute(update(InternalBot).where(InternalBot.id == compactor.id).values(is_default=False))
            return "committed"
        except OperationalError as error:
            assert error.orig.sqlstate == "55P03"
            return "blocked"

    def publish(db, current, data):
        if data["index"]["status"] == "indexed":
            # Separate native connection attempts revocation after the final
            # producer check, immediately before publishing the current pointer.
            with ThreadPoolExecutor(max_workers=1) as executor:
                outcomes.append(executor.submit(change_binding).result(timeout=3))
        return original(db, current, data)

    monkeypatch.setattr(index, "_cas", publish)
    assert await index.index_metadata(DomainService(), row.id)
    assert outcomes == ["blocked"]
    assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                 channel=CollaborationChannel.Api)
    assert change_binding() == "committed"
    assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                 channel=CollaborationChannel.Api) == []


@pytest.mark.parametrize("change", ["edit", "provider", "compactor", "permission"])
@pytest.mark.parametrize("mode", ["card", "board"])
async def test_query_rechecks_after_embedding_provider_returns(semantic, change, mode):
    _, actor, project, card, comment, binding, provider = semantic
    row = await generate(semantic)
    assert await index.index_metadata(DomainService(), row.id)
    def revoke():
        with DbSession.atomic() as db:
            if change == "edit":
                comment.content = EditorContentModel(content="corrected")
                db.update(comment)
            elif change == "provider":
                binding.is_default = False
                db.update(binding)
            elif change == "compactor":
                compactor = DomainService().internal_bot.get_comment_compaction_binding()
                compactor.is_default = False
                db.update(compactor)
            else:
                role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.project_id == project.id,
                    ProjectRole.user_id == actor.id)).first()
                role.actions = []
                db.update(role)
    provider.query_hook = revoke
    if mode == "board":
        assert DomainService().card.search_context_by_project(project, "availability interrupted", user=actor) == []
    else:
        assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                     channel=CollaborationChannel.Api) == []
    # Reconciliation retires the old pointer and records cleanup durably.
    assert await index.index_metadata(DomainService(), row.id) is False
    assert json.loads(metadata(card).value)["index"]["status"] == "retired"


async def test_legacy_metadata_queue_loss_recovery_and_stale_claim_cannot_publish(semantic, monkeypatch):
    _, actor, project, card, _, _, provider = semantic
    row = await generate(semantic)
    # Existing completed rows predate the index field; no schema/data migration is needed.
    with DbSession.atomic() as db:
        data = json.loads(row.value)
        del data["index"]
        row.value = json.dumps(data)
        db.update(row)
    queued = []
    monkeypatch.setattr(index, "enqueue_index", lambda uid: queued.append(uid) or True)
    assert index.recover_indexes() == 1 and queued == [row.id]
    def supersede():
        with DbSession.atomic() as db:
            current = index._row(db, row.id)
            data = json.loads(current.value)
            data["index"] = {"status": "running", "token": "new-owner", "expires_at": "2000-01-01"}
            assert index._cas(db, current, data)
    provider.document_hook = supersede
    assert await index.index_metadata(DomainService(), row.id) is False
    assert json.loads(metadata(card).value)["index"]["token"] == "new-owner"
    # Expired replacement is recoverable; persisted inference cache is reused safely.
    calls_before_retry = provider.document_calls
    assert calls_before_retry == 1
    assert await index.index_metadata(DomainService(), row.id)
    assert provider.document_calls == calls_before_retry
    assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                 channel=CollaborationChannel.Api)


async def test_persistent_cache_reused_across_distinct_compaction_sources(semantic):
    service, actor, project, card, _, _, provider = semantic
    first = await generate(semantic)
    assert await index.index_metadata(DomainService(), first.id)
    with DbSession.atomic() as db:
        other = Card(project_id=project.id, project_column_id=card.project_column_id, title="Different source")
        db.insert(other)
        comment = CardComment(card_id=other.id, user_id=actor.id, content=EditorContentModel(content="outage"))
        db.insert(comment)
    await service.generate(actor, project, other)
    second = metadata(other)
    assert await index.index_metadata(DomainService(), second.id)
    assert provider.document_calls == 1
    one = json.loads(metadata(card).value)["index"]["pointer"]
    two = json.loads(metadata(other).value)["index"]["pointer"]
    assert one["card_uid"] != two["card_uid"] and one["compaction_uid"] != two["compaction_uid"]
    remove_recorded_embedding(one)
    assert index.search_metadata(DomainService(), actor, project, other, "availability interrupted",
                                 channel=CollaborationChannel.Api)


async def test_worker_loss_after_vector_write_retains_durable_cleanup_receipt(semantic, monkeypatch):
    _, actor, project, card, _, _, _ = semantic
    row = await generate(semantic)
    original_write = index._write_vector_generation
    class WorkerLost(BaseException):
        pass
    def lost(store, chunks, pointer):
        original_write(store, chunks, pointer)
        raise WorkerLost()
    monkeypatch.setattr(index, "_write_vector_generation", lost)
    with pytest.raises(WorkerLost):
        await index.index_metadata(DomainService(), row.id)
    assert json.loads(metadata(card).value)["index"]["status"] == "running"
    with DbSession.use(readonly=False) as db:
        events = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()
    assert len(events) == 1 and events[0].pointer["compaction_uid"]
    assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                 channel=CollaborationChannel.Api) == []
    # The existing native cleanup consumer deletes the unpublished generation.
    remove_recorded_embedding(events[0].pointer)
    from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
    path = Env.DATA_DIR / "document-retrieval" / (events[0].pointer["embedding_fingerprint"] + ".sqlite")
    with open_sqlite_vector_store(path, SemanticProvider(), dimensions=3) as store:
        assert store.get_by_ids(events[0].pointer["chunk_ids"]) == []


async def test_unexpired_concurrent_claim_does_not_run_provider(semantic):
    _, _, _, card, _, _, provider = semantic
    row = await generate(semantic)
    with DbSession.atomic() as db:
        current = index._row(db, row.id)
        data = json.loads(current.value)
        data["index"] = {"status": "running", "token": "other-worker", "expires_at": "2999-01-01"}
        assert index._cas(db, current, data)
    assert await index.index_metadata(DomainService(), row.id) is False
    assert provider.document_calls == 0
    assert json.loads(metadata(card).value)["index"]["token"] == "other-worker"


@pytest.mark.parametrize("phase", ["index", "query"])
async def test_revoked_automatic_compactor_grant_cannot_publish(semantic, automatic_command, phase):  # noqa: F811
    service, actor, project, card, _, _, provider = semantic
    # The automatic fixture adds enough current comments to cross its threshold.
    binding = service._get_service_by_name("internal_bot").get_comment_compaction_binding()
    result = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    assert result["status"] == "completed"
    row = metadata(card)
    from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
    def revoke():
        with DbSession.atomic() as db:
            grant = db.exec(SqlBuilder.select.table(CompactorBoardGrant)).first()
            grant.can_read = grant.can_compact = grant.can_archive = False
            db.update(grant)
    if phase == "query":
        assert await index.index_metadata(DomainService(), row.id)
        provider.query_hook = revoke
        assert DomainService().card.search_context_by_project(project, "availability interrupted", user=actor) == []
    else:
        provider.document_hook = revoke
    assert await index.index_metadata(DomainService(), row.id) is False
    assert json.loads(metadata(card).value)["index"]["status"] == "retired"


async def test_authenticated_mcp_discovers_and_calls_semantic_metadata_tool(semantic, monkeypatch):
    from types import SimpleNamespace
    from fastmcp import Client
    from langboard.mcp_integration.Server import McpServer
    from langboard.mcp_integration.Tool import McpTool
    from langboard.mcp_tools import CardMcp
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
    from langboard_shared.core.db import BaseDbModel

    assert CardMcp.search_card_compaction_metadata
    _, actor, project, card, _, _, _ = semantic
    BaseDbModel.metadata.create_all(DbEngine.get_main_engine())
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    row = await generate(semantic)
    assert await index.index_metadata(DomainService(), row.id)
    name = "search_card_compaction_metadata"
    registered = {name: McpTool.get_tool(name)}
    monkeypatch.setattr(McpTool, "get_tools", lambda: registered)
    monkeypatch.setattr(McpTool, "get_tool", lambda key: registered.get(key))
    _, server = McpServer.get_http_app("agent")
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=[name]), "user_or_bot": actor})
    try:
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert name in tools and "query" in tools[name].inputSchema["properties"]
            receipt = await client.call_tool(name, {"project_uid": project.get_uid(), "card_uid": card.get_uid(),
                "query": "availability interrupted"})
            assert not receipt.is_error, receipt
            hit = receipt.data["matches"][0]
            assert hit["similarity_score"] > 0.99 and hit["compaction_uid"]
            assert hit["source"]["source_type"] == "comment_compaction"
    finally:
        mcp_auth_context.reset(token)


async def test_board_semantic_query_finds_oldest_relevant_source_with_one_native_query(semantic, monkeypatch):
    from langboard_shared.tasks.docling.DocumentLangGraphVectorStore import DocumentLangGraphVectorStore

    service, actor, project, card, _, _, provider = semantic
    first = await generate(semantic)
    assert await index.index_metadata(DomainService(), first.id)
    for number in range(20):
        with DbSession.atomic() as db:
            other = Card(project_id=project.id, project_column_id=card.project_column_id, title=f"Newer source {number}")
            db.insert(other)
            db.insert(CardComment(card_id=other.id, user_id=actor.id, content=EditorContentModel(content="weather")))
        await service.generate(actor, project, other)
        assert await index.index_metadata(DomainService(), metadata(other).id)
    assert provider.document_calls == 2
    calls = []
    original = DocumentLangGraphVectorStore.similarity_search_with_score
    def count(store, *args, **kwargs):
        calls.append(kwargs)
        return original(store, *args, **kwargs)
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", count)
    results = DomainService().card.search_context_by_project(project, "availability interrupted", user=actor)
    assert [result["uid"] for result in results] == [card.get_uid()]
    hit = results[0]["search_metadata"]["similarity"][0]
    assert hit["similarity_score"] > 0.99 and "availability interrupted" not in hit["content"]
    assert len(calls) == 1 and provider.query_calls == 1
    assert calls[0]["k"] <= 100
    assert calls[0]["filter"]["board_uid"] == project.get_uid()
    assert "card_uid" not in calls[0]["filter"]
    # Existing literal lookup still finds metadata using its native SQL path.
    matches = service.search_metadata_candidates(actor, project,
        service._get_service_by_name("card").resolve_visibility_context(project, actor, CollaborationChannel.Api)[1], "outage")
    assert card.id in matches


async def test_existing_periodic_document_recovery_redelivers_lost_compaction_work(semantic, monkeypatch):
    import runpy
    from types import SimpleNamespace
    from unittest.mock import Mock
    from langboard_shared.domain import services
    from langboard_shared.tasks.docling import DocumentCleanupTask, DocumentKeywordTask

    row = await generate(semantic)
    queued = []
    monkeypatch.setattr(index, "enqueue_index", lambda uid: queued.append(uid) or True)
    monkeypatch.setattr(DocumentCleanupTask, "recover_cleanup", Mock(return_value=0))
    monkeypatch.setattr(DocumentKeywordTask, "recover_keyword", Mock(return_value=0))
    monkeypatch.setattr(services, "DomainService", lambda: SimpleNamespace(
        docling_metadata=SimpleNamespace(recover_card_location_embeddings=lambda **_: 0), close=lambda: None))
    runpy.run_module("langboard_shared.tasks.docling.DocumentRecovery", run_name="__main__")
    assert queued == [row.id]
    DocumentCleanupTask.recover_cleanup.assert_called_once()
    DocumentKeywordTask.recover_keyword.assert_called_once()


async def test_board_query_skips_private_invalid_replaced_and_foreign_hits_before_oldest_valid(semantic, monkeypatch):
    from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectColumn
    from langboard_shared.tasks.docling.DocumentLangGraphVectorStore import DocumentLangGraphVectorStore

    service, actor, project, oldest, _, _, provider = semantic
    first = await generate(semantic)
    assert await index.index_metadata(DomainService(), first.id)
    sources = {}
    for kind in ("private", "edited", "replaced", "foreign"):
        target = project
        column_id = oldest.project_column_id
        with DbSession.atomic() as db:
            if kind == "foreign":
                target = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
                db.insert(ProjectAssignedUser(project_id=target.id, user_id=actor.id))
                db.insert(ProjectRole(project_id=target.id, user_id=actor.id, actions=["read", "card_update"]))
                column = ProjectColumn(project_id=target.id, name="Foreign")
                db.insert(column)
                column_id = column.id
            card = Card(project_id=target.id, project_column_id=column_id, title=f"Candidate {kind}",
                        visibility="INTERNAL",
                        created_by_user_id=actor.id)
            db.insert(card)
            comment = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="outage"))
            db.insert(comment)
        assert DomainService().card.resolve_readable_card(target, card, actor, CollaborationChannel.Api) is not None, kind
        await service.generate(actor, target, card)
        row = metadata(card)
        assert await index.index_metadata(DomainService(), row.id)
        sources[kind] = (card, comment, row)
    with DbSession.atomic() as db:
        private = sources["private"][0]
        private.visibility = "PRIVATE"
        private.owner_user_id = private.created_by_user_id = 2
        db.update(private)
        edited = sources["edited"][1]
        edited.content = EditorContentModel(content="corrected evidence")
        db.update(edited)
        replaced = index._row(db, sources["replaced"][2].id)
        value = json.loads(replaced.value)
        value["index"]["pointer"]["generation"] = "new-authoritative-generation"
        assert index._cas(db, replaced, value)
    calls = []
    original = DocumentLangGraphVectorStore.similarity_search_with_score
    def hostile_candidates(store, *args, **kwargs):
        hits = original(store, *args, **kwargs)
        calls.append(1)
        foreign_pointer = json.loads(metadata(sources["foreign"][0]).value)["index"]["pointer"]
        assert all(doc.metadata["board_uid"] == project.get_uid() for doc, _ in hits)
        foreign_doc = store.get_by_ids(foreign_pointer["chunk_ids"])[0]
        # Inject one foreign payload defensively, then order actual native hits so
        # denied/stale generations precede the valid oldest candidate.
        order = {sources[kind][0].get_uid(): number for number, kind in enumerate(("private", "edited", "replaced"))}
        hits.sort(key=lambda hit: order.get(hit[0].metadata["card_uid"], 10))
        return [(foreign_doc, 1.0), *hits]
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", hostile_candidates)
    results = DomainService().card.search_context_by_project(project, "availability interrupted", user=actor)
    assert [item["uid"] for item in results] == [oldest.get_uid()]
    assert calls == [1] and provider.query_calls == 1


async def test_direct_card_query_finds_oldest_of_21_valid_disjoint_compactions(semantic, monkeypatch):
    from langboard_shared.tasks.docling.DocumentLangGraphVectorStore import DocumentLangGraphVectorStore

    service, actor, project, card, _, _, provider = semantic
    first = await generate(semantic)
    first_uid = json.loads(first.value)["compaction_uid"]
    assert await index.index_metadata(DomainService(), first.id)
    compactor = DomainService().internal_bot.get_comment_compaction_binding()
    repository = service.repo.card_comment_compaction
    # Independent source ranges can complete concurrently; no cumulative-summary
    # invariant forbids these 21 valid, nonoverlapping native records.
    for _ in range(20):
        with DbSession.atomic() as db:
            comment = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="weather"))
            db.insert(comment)
        snapshot, _ = service._snapshot(project, card, refs=[f"comment:{comment.get_uid()}"])
        refs = [source.evidence_ref for source in snapshot.sources]
        draft = CompactionDraft(claims=[{"text": "Separate report", "kind": "unresolved", "evidence_refs": refs}],
            keywords=[{"value": "weather", "evidence_refs": refs}])
        record = repository.reserve(card, actor, compactor, snapshot)
        claimed = repository.claim(record)
        assert repository.complete(record, claimed.attempt, snapshot, compactor, draft)
        assert await index.index_metadata(DomainService(), metadata(card).id)
    assert len(service._valid_blocks(actor, project, card, channel=CollaborationChannel.Api)) == 21
    calls = []
    original = DocumentLangGraphVectorStore.similarity_search_with_score
    def count(store, *args, **kwargs):
        calls.append(kwargs)
        return original(store, *args, **kwargs)
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", count)
    hits = index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                 channel=CollaborationChannel.Api)
    assert [hit["compaction_uid"] for hit in hits] == [first_uid]
    assert len(calls) == 1 and provider.query_calls == 1
    assert calls[0]["filter"]["card_uid"] == card.get_uid()


@pytest.mark.parametrize("scope", ["board", "card"])
@pytest.mark.parametrize("state", ["empty", "pending"])
async def test_unindexed_catalog_avoids_shared_store_query_and_provider(semantic, monkeypatch, scope, state):
    from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectColumn
    from langboard_shared.tasks.docling.DocumentLangGraphVectorStore import DocumentLangGraphVectorStore

    service, actor, project, card, _, _, provider = semantic
    if scope == "board":
        # Another board creates the exact shared fingerprint store first.
        with DbSession.atomic() as db:
            other_project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
            db.insert(ProjectAssignedUser(project_id=other_project.id, user_id=actor.id))
            db.insert(ProjectRole(project_id=other_project.id, user_id=actor.id, actions=["read", "card_update"]))
            column = ProjectColumn(project_id=other_project.id, name="Other board")
            db.insert(column)
            other = Card(project_id=other_project.id, project_column_id=column.id, title="Indexed elsewhere")
            db.insert(other)
            db.insert(CardComment(card_id=other.id, user_id=actor.id, content=EditorContentModel(content="outage")))
        await service.generate(actor, other_project, other)
    else:
        # A different card on the same board owns the existing store.
        with DbSession.atomic() as db:
            other = Card(project_id=project.id, project_column_id=card.project_column_id, title="Indexed elsewhere")
            db.insert(other)
            db.insert(CardComment(card_id=other.id, user_id=actor.id, content=EditorContentModel(content="outage")))
        await service.generate(actor, project, other)
    assert await index.index_metadata(DomainService(), metadata(other).id)
    if state == "pending":
        await generate(semantic)
    calls = []
    original = DocumentLangGraphVectorStore.similarity_search_with_score
    def count(store, *args, **kwargs):
        calls.append(1)
        return original(store, *args, **kwargs)
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", count)
    if scope == "board":
        assert index.search_board_metadata(DomainService(), actor, project, "availability interrupted",
                                           channel=CollaborationChannel.Api) == {}
    else:
        assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted",
                                     channel=CollaborationChannel.Api) == []
    assert calls == [] and provider.query_calls == 0


def test_periodic_recovery_imports_and_runs_without_optional_retrieval_dependencies(tmp_path):
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""\
        import importlib
        import importlib.abc
        import runpy
        import sys
        from pathlib import Path
        from langboard_shared.Env import Env
        type(Env).DATA_DIR = property(lambda _: Path(sys.argv[1]))

        class NoRetrieval(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in {"langchain_core", "langgraph", "tiktoken"}:
                    raise ModuleNotFoundError("Optional retrieval dependency blocked: " + fullname)
        sys.meta_path.insert(0, NoRetrieval())
        package = "langboard_shared.tasks.docling."
        task = importlib.import_module(package + "CompactionMetadataIndexTask")
        cleanup = importlib.import_module(package + "DocumentCleanupTask")
        keyword = importlib.import_module(package + "DocumentKeywordTask")
        services = importlib.import_module("langboard_shared.domain.services")
        calls = []
        task.recover_indexes = lambda: calls.append("index")
        cleanup.recover_cleanup = lambda: calls.append("cleanup")
        keyword.recover_keyword = lambda: calls.append("keyword")
        class Service:
            @property
            def docling_metadata(self):
                return self
            def recover_card_location_embeddings(self, **kwargs):
                calls.append("location")
            def close(self):
                calls.append("close")
        services.DomainService = Service
        runpy.run_module(package + "DocumentRecovery", run_name="__main__")
        assert calls == ["index", "cleanup", "keyword", "location", "close"], calls
        assert not any(name.split(".")[0] in {"langchain_core", "langgraph", "tiktoken"} for name in sys.modules)
        print("base recovery completed without optional retrieval dependencies")
    """)
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "base recovery completed" in result.stdout


async def test_automatic_compaction_indexes_with_exact_authority(semantic, automatic_command):  # noqa: F811
    service, actor, project, card, *_ = semantic
    binding = service._get_service_by_name("internal_bot").get_comment_compaction_binding()
    assert (await service.generate_automatic(project, binding, card,
        rule_id=rule_id(automatic_command[2])))["status"] == "completed"
    row = metadata(card)
    authority = json.loads(row.value)["automatic_authority"]
    assert authority["rule_uid"]
    assert authority["rule_revision"] == 1
    assert await index.index_metadata(DomainService(), row.id)
    assert DomainService().card.search_context_by_project(project, "availability interrupted", user=actor)
    assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted", channel=CollaborationChannel.Api)


@pytest.mark.parametrize("change", ["retired_rule", "changed_rule", "residency", "missing_provenance"])
@pytest.mark.parametrize("phase", ["index", "query"])
async def test_automatic_index_revalidates_original_authority(semantic, automatic_command, change, phase):  # noqa: F811
    from datetime import timedelta
    from langboard_shared.domain.models import WorkflowRule

    service, actor, project, card, _, _, provider = semantic
    binding = service._get_service_by_name("internal_bot").get_comment_compaction_binding()
    original_rule_id = rule_id(automatic_command[2])
    assert (await service.generate_automatic(project, binding, card, rule_id=original_rule_id))["status"] == "completed"
    row = metadata(card)

    def invalidate():
        with DbSession.atomic() as db:
            if change in {"retired_rule", "changed_rule"}:
                rule = db.exec(SqlBuilder.select.table(WorkflowRule).where(WorkflowRule.id == original_rule_id)).first()
                if change == "retired_rule":
                    rule.retired = True
                else:
                    rule.revision += 1
                db.update(rule)
            elif change == "residency":
                current = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
                current.workflow_stage_entered_at += timedelta(seconds=1)
                db.update(current)
            else:
                current = db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.id == row.id)).first()
                data = json.loads(current.value)
                del data["automatic_authority"]
                current.value = json.dumps(data)
                db.update(current)

    if phase == "query":
        assert await index.index_metadata(DomainService(), row.id)
        provider.query_hook = invalidate
        assert DomainService().card.search_context_by_project(project, "availability interrupted", user=actor) == []
        assert index.search_metadata(DomainService(), actor, project, card, "availability interrupted", channel=CollaborationChannel.Api) == []
    else:
        invalidate()
        assert await index.index_metadata(DomainService(), row.id) is False
        assert provider.document_calls == 0
