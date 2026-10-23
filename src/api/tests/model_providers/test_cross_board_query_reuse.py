# ruff: noqa: F811
"""Persisted cross-board native retrieval shares only a verified request lifetime."""

import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import (
    Card,
    CardComment,
    InternalBot,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.tasks.docling.DocumentLangGraphVectorStore import DocumentLangGraphVectorStore
from test_compaction_semantic_index import command, compact_command, generate, index, metadata, semantic  # noqa: F401


QUERY = "availability interrupted"
CHANNEL = CollaborationChannel.Api


@pytest.fixture
async def boards(semantic, monkeypatch):
    service, actor, first, card, _, binding, provider = semantic
    with DbSession.atomic() as db:
        second = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
        db.insert(ProjectAssignedUser(project_id=second.id, user_id=actor.id))
        db.insert(ProjectRole(project_id=second.id, user_id=actor.id, actions=["read", "card_update"]))
        column = ProjectColumn(project_id=second.id, name="Second readable board")
        db.insert(column)
        other = Card(project_id=second.id, project_column_id=column.id, title="Second semantic result")
        db.insert(other)
        db.insert(CardComment(card_id=other.id, user_id=actor.id, content=EditorContentModel(content="outage")))
    await generate(semantic)
    assert (await service.generate(actor, second, other))["status"] == "completed"
    native = DomainService()
    for candidate in (card, other):
        assert await index.index_metadata(native, metadata(candidate).id)
    import importlib
    card_module = importlib.import_module("langboard_shared.domain.services.factory.CardService")
    monkeypatch.setattr(card_module, "search_document_candidates_many", lambda *_: None)
    counters = {"providers": 0, "opened": 0, "closed": 0, "provider_closed": 0, "queries": []}
    original_open = index.open_document_vector_store
    original_search = DocumentLangGraphVectorStore.similarity_search_with_score

    def create(*_args):
        counters["providers"] += 1
        return provider

    def close():
        counters["provider_closed"] += 1

    @contextmanager
    def opened(*args, **kwargs):
        counters["opened"] += 1
        try:
            with original_open(*args, **kwargs) as store:
                yield store
        finally:
            counters["closed"] += 1

    def search(store, *args, **kwargs):
        counters["queries"].append(kwargs)
        return original_search(store, *args, **kwargs)

    monkeypatch.setattr(index, "create_document_embeddings", create)
    monkeypatch.setattr(index, "open_document_vector_store", opened)
    monkeypatch.setattr(provider, "close", close, raising=False)
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", search)
    return SimpleNamespace(native=native, actor=actor, projects=(first, second), cards=(card, other),
                           binding=binding, provider=provider, counts=counters)


async def test_two_board_request_reuses_query_and_store_without_result_loss(boards):
    b = boards
    for request_count in (1, 2):
        result = b.native.card.search_context_by_project(None, QUERY, user=b.actor, paginated=True)
        assert {row["uid"] for row in result["cards"]} == {card.get_uid() for card in b.cards}
        assert result["total_count"] == 2
        assert all(row["search_metadata"]["similarity"] for row in result["cards"])
        assert b.provider.query_calls == request_count
        assert b.counts["providers"] == b.counts["opened"] == b.counts["closed"] == request_count
        assert b.counts["provider_closed"] == request_count
        assert len(b.counts["queries"]) == 2 * request_count
    assert all(call["k"] == 100 for call in b.counts["queries"])
    assert {call["filter"]["board_uid"] for call in b.counts["queries"]} == {p.get_uid() for p in b.projects}
    # Existing direct card API retains its own short lifetime.
    assert index.search_metadata(b.native, b.actor, b.projects[0], b.cards[0], QUERY, channel=CHANNEL)
    assert b.provider.query_calls == 3 and b.counts["closed"] == 3


@pytest.mark.parametrize("change", ["revision", "credential", "model", "settings", "endpoint", "dimensions", "identity"])
async def test_changed_verified_binding_cannot_reuse_within_request(boards, monkeypatch, change):
    b = boards
    with index.SemanticSearchRequest() as request:
        assert index.search_board_metadata(b.native, b.actor, b.projects[0], QUERY, channel=CHANNEL, request=request)
        with DbSession.atomic() as db:
            binding = db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.id == b.binding.id)).first()
            value = json.loads(binding.value)
            if change == "revision":
                binding.updated_at += timedelta(seconds=1)
                db.update(binding)
            elif change == "identity":
                binding.is_default = False
                db.update(binding)
                binding = InternalBot(bot_type=binding.bot_type, is_default=True, platform=binding.platform,
                    platform_running_type=binding.platform_running_type, display_name="Replacement", value=binding.value)
                db.insert(binding)
            else:
                if change == "credential":
                    value["api_key"] = "rotated-fixture"
                elif change == "model":
                    value["model_name"] = "changed-fixture"
                elif change == "endpoint":
                    value["base_url"] = "http://changed.invalid/v1"
                elif change == "dimensions":
                    value["retrieval"]["dimensions"] = 4
                    monkeypatch.setattr(b.provider, "vector", lambda _: [1.0, 0.0, 0.01, 0.0])
                else:
                    value["retrieval"]["score_threshold"] = 0.8
                # A config change must invalidate even if a writer preserves its timestamp.
                from sqlalchemy import update
                db.exec(update(InternalBot).where(InternalBot.id == binding.id).values(
                    value=json.dumps(value), updated_at=binding.updated_at))
        # Publish legitimate new generations under the changed current binding.
        assert await index.index_metadata(b.native, metadata(b.cards[1]).id)
        # Ignore producer calls: acceptance counts only query inference and search stores.
        before = (b.counts["providers"], b.counts["opened"])
        assert index.search_board_metadata(b.native, b.actor, b.projects[1], QUERY, channel=CHANNEL, request=request)
        assert b.provider.query_calls == 2
        assert b.counts["providers"] == before[0] + 1
        assert b.counts["opened"] == before[1] + 1
    assert b.counts["opened"] == b.counts["closed"]
    assert request.stores == {}


async def test_provider_failure_is_not_retried_for_each_board_and_resources_close(boards):
    b = boards
    def failed(_):
        raise RuntimeError("fixture inference failure")
    b.provider.query_hook = lambda: failed(None)
    result = b.native.card.search_context_by_project(None, QUERY, user=b.actor, paginated=True)
    assert result["cards"] == [] and result["total_count"] == 0
    assert b.provider.query_calls == 1
    assert b.counts["opened"] == b.counts["closed"] == b.counts["provider_closed"] == 1
    assert len(b.counts["queries"]) == 2
    # A fresh request can recover; failure state does not escape its lifetime.
    recovered = b.native.card.search_context_by_project(None, QUERY, user=b.actor)
    assert len(recovered) == 2 and b.provider.query_calls == 2


async def test_unindexed_board_does_not_open_or_infer(boards):
    b = boards
    with DbSession.atomic() as db:
        row = index._row(db, metadata(b.cards[1]).id)
        data = json.loads(row.value)
        data["index"] = {"status": "pending"}
        assert index._cas(db, row, data)
    result = b.native.card.search_context_by_project(None, QUERY, user=b.actor, paginated=True)
    assert [row["uid"] for row in result["cards"]] == [b.cards[0].get_uid()]
    assert b.provider.query_calls == 1 and len(b.counts["queries"]) == 1
    assert b.counts["opened"] == b.counts["closed"] == 1


@pytest.mark.parametrize("mutation", ["reader", "source"])
async def test_later_store_query_revalidates_earlier_authority_and_source(boards, monkeypatch, mutation):
    b = boards
    original = DocumentLangGraphVectorStore.similarity_search_with_score
    def revoke(store, *args, **kwargs):
        hits = original(store, *args, **kwargs)
        if kwargs["filter"]["board_uid"] == b.projects[1].get_uid():
            with DbSession.atomic() as db:
                if mutation == "reader":
                    role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                        ProjectRole.project_id == b.projects[0].id, ProjectRole.user_id == b.actor.id)).first()
                    role.actions = []
                    db.update(role)
                else:
                    comment = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.card_id == b.cards[0].id)).first()
                    comment.content = EditorContentModel(content="corrected source")
                    db.update(comment)
        return hits
    monkeypatch.setattr(DocumentLangGraphVectorStore, "similarity_search_with_score", revoke)
    result = b.native.card.search_context_by_project(None, QUERY, user=b.actor, paginated=True)
    assert [row["uid"] for row in result["cards"]] == [b.cards[1].get_uid()]
    assert result["total_count"] == 1 and b.provider.query_calls == 1
    assert b.counts["opened"] == b.counts["closed"] == 1


async def test_failed_store_open_is_shared_and_provider_closed(boards, monkeypatch):
    b = boards
    opens = []
    @contextmanager
    def unavailable(*args, **kwargs):
        opens.append(1)
        raise RuntimeError("fixture store unavailable")
        yield  # pragma: no cover
    monkeypatch.setattr(index, "open_document_vector_store", unavailable)
    result = b.native.card.search_context_by_project(None, QUERY, user=b.actor)
    assert result == [] and opens == [1]
    assert b.counts["providers"] == b.counts["provider_closed"] == 1
    assert b.provider.query_calls == 0


async def test_request_exception_closes_native_store_and_discards_vectors(boards):
    b = boards
    with pytest.raises(RuntimeError, match="request interrupted"):
        with index.SemanticSearchRequest() as request:
            assert index.search_board_metadata(b.native, b.actor, b.projects[0], QUERY, channel=CHANNEL, request=request)
            raise RuntimeError("request interrupted")
    assert request.stores == {}
    assert b.counts["opened"] == b.counts["closed"] == b.counts["provider_closed"] == 1


@pytest.mark.parametrize("failure", ["provider", "store"])
async def test_optional_resource_close_failure_preserves_native_search(boards, monkeypatch, failure):
    b = boards
    with DbSession.atomic() as db:
        for card in b.cards:
            card.title = QUERY
            db.update(card)
    if failure == "provider":
        original_close = b.provider.close

        def close():
            original_close()
            raise RuntimeError("fixture cleanup failure")

        monkeypatch.setattr(b.provider, "close", close)
    else:
        original_open = index.open_document_vector_store

        @contextmanager
        def opened(*args, **kwargs):
            with original_open(*args, **kwargs) as store:
                yield store
            raise RuntimeError("fixture cleanup failure")

        monkeypatch.setattr(index, "open_document_vector_store", opened)
    result = b.native.card.search_context_by_project(None, QUERY, user=b.actor, paginated=True)
    assert {row["uid"] for row in result["cards"]} == {card.get_uid() for card in b.cards}
    assert result["total_count"] == 2
    assert b.counts["opened"] == b.counts["closed"] == b.counts["provider_closed"] == 1
