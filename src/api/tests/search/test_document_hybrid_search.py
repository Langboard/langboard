# ruff: noqa: F811
"""Native card search integrates configured vectors without indexing on reads."""

import json
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import CardAttachment, CardMetadata, InternalBot, ProjectAssignedUser, ProjectRole
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services.factory.DoclingMetadataService import DOCLING_DOCUMENTS_METADATA_KEY
from langboard_shared.Env import Env
from langboard_shared.tasks.docling import CompactionMetadataIndexTask as semantic
from langboard_shared.tasks.docling.DocumentEmbedding import snapshot_embedding_config
from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
from langboard_shared.tasks.docling.DocumentSqliteVectorStore_test import Fixture
from langboard_shared.tasks.docling.DocumentVectorGeneration import embedding_fingerprint
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sqlalchemy import delete, update
from src.api.tests.search.test_keyword_search_connection import document_board  # noqa: F401


class LocalQuery(Embeddings):
    def __init__(self):
        self.queries = []
        self.before = None

    def embed_query(self, text):
        self.queries.append(text)
        if self.before:
            self.before()
        return [1.0, 0.0, 0.1]

    def embed_documents(self, texts):
        raise AssertionError("Search must never embed attachment documents")


@pytest.fixture
def indexed(document_board, monkeypatch, tmp_path):
    service, actor, project, sources, _ = document_board
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED", "false")
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path))
    config = dict(agent_llm="OpenAI", base_url="https://fixture.invalid/v1", model_name="fixture",
                  retrieval=dict(enabled=True, store="sqlite", dimensions=3))
    with DbSession.use(readonly=False) as db:
        binding = InternalBot(bot_type=InternalBotType.DocumentEmbedding, is_default=True,
                              display_name="Fixture", platform=BotPlatform.Default,
                              platform_running_type=BotPlatformRunningType.Default, value=json.dumps(config))
        db.insert(binding)
    snapshot = snapshot_embedding_config(binding.value, binding.get_uid())
    fingerprint = embedding_fingerprint(provider=config["base_url"], model="fixture", dimensions=3, version="v1")
    directory = tmp_path / "document-retrieval"
    directory.mkdir()
    provider = LocalQuery()
    states = {}
    with open_sqlite_vector_store(directory / (fingerprint + ".sqlite"), Fixture(), dimensions=3) as store:
        for name, (card, attachment) in sources.items():
            source = dict(board_uid=project.get_uid(), card_uid=card.get_uid(), attachment_uid=attachment.get_uid(),
                          content_hash="b" * 64, embedding_fingerprint=fingerprint, generation="vector-current")
            identifier = str(uuid5(NAMESPACE_URL, name))
            pointer = {**source, "chunk_ids": [identifier], "storage": {"type": "sqlite"}}
            # Setup writes precomputed vectors; the read path receives a provider forbidding document embedding.
            store.add_documents([Document(page_content=f"semantic evidence for {name} " + "한글 " * 300,
                                          metadata={**source, "page": 3, "pages": [3, 4], "provenance_scope": "page"})],
                                ids=[identifier])
            state = dict(attachment_uid=attachment.get_uid(), generation="transcription-current", status="indexed",
                         content_hash="b" * 64, embedding_config=snapshot,
                         embedding=dict(status="indexed", source_generation="transcription-current",
                                        pointer=pointer, config=snapshot))
            states[name] = state
            if name == "Wrong link":
                pointer["card_uid"] = sources["Visible"][0].get_uid()
            if name == "Stale":
                state["embedding"]["source_generation"] = "old"
            with DbSession.use(readonly=False) as db:
                db.exec(update(CardAttachment).where(CardAttachment.id == attachment.id).values(content_sha256="b" * 64))
                db.insert(CardMetadata(card_id=card.id, key=DOCLING_DOCUMENTS_METADATA_KEY, value=json.dumps([state])))
        # A stale chunk in the same source must not pass the active pointer fence.
        original = states["Visible"]["embedding"]["pointer"]
        store.add_documents([Document(page_content="stale secret",
                                      metadata={**original, "generation": "old", "page": 99})], ids=[str(uuid5(NAMESPACE_URL, "old-chunk"))])
    monkeypatch.setattr(semantic, "create_document_embeddings", lambda *_: provider)
    return service, actor, project, sources, states, provider, binding


def search(indexed, **kwargs):
    service, actor, project, *_ = indexed
    return service.card.search_context_by_project(project, "billing", user=actor,
                                                 channel=CollaborationChannel.HumanUI, **kwargs)


def test_configured_native_vector_results_and_provenance(indexed):
    results = search(indexed, paginated=True, limit=20)
    titles = {card["title"] for card in results["cards"]}
    assert {"Visible", "Own private", "billing from PostgreSQL"} <= titles
    assert not titles & {"Internal", "Private", "Foreign", "Deleted", "Stale", "Deleted attachment",
                         "Foreign workflow", "Removed workflow", "Wrong link"}
    visible = next(card for card in results["cards"] if card["title"] == "Visible")
    match = visible["document_matches"][0]
    assert match["attachment_uid"] == indexed[3]["Visible"][1].get_uid()
    assert match["page"] == 3 and match["pages"] == [3, 4] and match["provenance_scope"] == "page"
    assert match["generation"] == "vector-current" and match["content_hash"] == "b" * 64
    assert match["chunk_id"] == str(uuid5(NAMESPACE_URL, "Visible")) and len(match["snippet"]) <= 500
    assert "stale secret" not in json.dumps(results)
    assert all(len(card.get("document_matches", [])) <= 2 for card in results["cards"])
    assert indexed[5].queries == ["billing"]
    bounded = search(indexed, paginated=True, limit=1)
    assert len(bounded["cards"]) == 1 and bounded["total_count"] == results["total_count"]


@pytest.mark.parametrize("phase", ["query", "later_provider", "metadata_validation"])
@pytest.mark.parametrize("change", ["membership", "read_role", "attachment", "generation", "binding"])
def test_current_grants_and_generation_rechecked_after_query(indexed, monkeypatch, change, phase):
    _, actor, project, sources, states, provider, binding = indexed

    def revoke():
        with DbSession.use(readonly=False) as db:
            if change == "membership":
                db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == project.id,
                                                          ProjectAssignedUser.user_id == actor.id))
            elif change == "read_role":
                db.exec(update(ProjectRole).where(ProjectRole.project_id == project.id,
                                                  ProjectRole.user_id == actor.id).values(actions=[]))
            elif change == "attachment":
                from langboard_shared.core.types import SafeDateTime
                db.exec(update(CardAttachment).where(CardAttachment.id == sources["Visible"][1].id)
                        .values(deleted_at=SafeDateTime.now()))
            elif change == "generation":
                state = deepcopy(states["Visible"])
                state["embedding"]["pointer"]["generation"] = "replacement"
                db.exec(update(CardMetadata).where(CardMetadata.card_id == sources["Visible"][0].id)
                        .values(value=json.dumps([state])))
            else:
                config = json.loads(binding.value)
                config["retrieval"]["enabled"] = False
                db.exec(update(InternalBot).where(InternalBot.id == binding.id).values(value=json.dumps(config)))

    if phase == "query":
        provider.before = revoke
    else:
        from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
        if phase == "metadata_validation":
            monkeypatch.setattr(CardCommentCompactionService, "search_metadata_candidates",
                                lambda *_args, **_kwargs: {sources["Visible"][0].id: {"similarity": []}})
            def metadata_validation(*_args, **_kwargs):
                revoke()
                return {"keywords": [], "entities": []}
            monkeypatch.setattr(CardCommentCompactionService, "get_search_metadata", metadata_validation)
        else:
            def later_provider(*_args, **_kwargs):
                revoke()
                return {}
            monkeypatch.setattr(CardCommentCompactionService, "search_metadata_candidates", later_provider)
    response = search(indexed, paginated=change == "read_role")
    results = response["cards"] if change == "read_role" else response
    assert "Visible" not in {card["title"] for card in results}
    if change in {"membership", "read_role"}:
        assert results == []
    else:
        assert "billing from PostgreSQL" in {card["title"] for card in results}


def test_unconfigured_and_failed_vector_provider_keep_keyword_fallback(indexed):
    _, _, _, _, _, provider, binding = indexed
    provider.before = lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable"))
    assert [card["title"] for card in search(indexed)] == ["billing from PostgreSQL"]
    with DbSession.use(readonly=False) as db:
        db.exec(delete(InternalBot).where(InternalBot.id == binding.id))
    provider.queries.clear()
    assert [card["title"] for card in search(indexed)] == ["billing from PostgreSQL"]
    assert provider.queries == []


def test_document_token_budget_is_shared_across_board_queries(indexed):
    from types import SimpleNamespace
    import tiktoken
    from langboard_shared.tasks.docling.CompactionMetadataIndexTask import SemanticSearchRequest
    from langboard_shared.tasks.docling.DocumentVectorQuery import search_board_documents

    service, actor, project, _, _, _, binding = indexed
    config = json.loads(binding.value)
    config["retrieval"]["max_return_tokens"] = 128
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBot).where(InternalBot.id == binding.id).values(value=json.dumps(config)))
    _, context = service.card.resolve_visibility_context(project, actor, CollaborationChannel.HumanUI)
    facade = SimpleNamespace(internal_bot=service.internal_bot)
    with SemanticSearchRequest() as request:
        first = search_board_documents(facade, service.card.repo.card, project, "billing", context=context, request=request)
        assert first
        assert sum(len(tiktoken.get_encoding("cl100k_base").encode(item["hit"]["content"])) for item in first) <= 128
        assert request.document_remaining_tokens == 0
        assert search_board_documents(facade, service.card.repo.card, project, "billing", context=context, request=request) == []


def test_unavailable_vector_store_is_not_retried_per_board_in_same_request(indexed, monkeypatch):
    from types import SimpleNamespace
    from langboard_shared.tasks.docling.CompactionMetadataIndexTask import SemanticSearchRequest
    from langboard_shared.tasks.docling.DocumentVectorQuery import search_board_documents

    service, actor, project, *_ = indexed
    _, context = service.card.resolve_visibility_context(project, actor, CollaborationChannel.HumanUI)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("fixture vector store unavailable")

    monkeypatch.setattr(SemanticSearchRequest, "store", lambda *args: SimpleNamespace(similarity_search_with_score=unavailable))
    facade = SimpleNamespace(internal_bot=service.internal_bot)
    with SemanticSearchRequest() as request:
        for _ in range(3):
            assert search_board_documents(facade, service.card.repo.card, project, "billing", context=context, request=request) == []
    assert calls == [1]
    # A subsequent user search gets one fresh attempt; no persistent circuit state.
    with SemanticSearchRequest() as request:
        assert search_board_documents(facade, service.card.repo.card, project, "billing", context=context, request=request) == []
    assert calls == [1, 1]
