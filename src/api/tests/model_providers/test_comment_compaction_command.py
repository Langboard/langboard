"""The native command collects its own primary evidence and rechecks every output."""

import asyncio
import importlib
import json
from datetime import timedelta
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.board import BoardCardCommentCompactionApi  # noqa: F401
from langboard_shared.ai.comment_compaction import CommentCompactionSnapshot, CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.security.GraphServiceAuth import verify_graph_service_token
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, CardComment, CardCommentCompaction, ProjectActivity, User
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardCommentCompactionService import CommentCompactionError
from langboard_shared.Env import Env


command = importlib.import_module("langboard_shared.domain.services.factory.CardCommentCompactionService")


@pytest.fixture
def compact_command(compactions, board, monkeypatch):
    repo, actor, card, binding, comment, *_ = compactions
    ProjectActivity.__table__.create(DbEngine.get_main_engine())
    with DbSession.atomic() as db:
        board[4].actions = ["read", "card_update"]
        db.update(board[4])
        binding.is_default = True
        binding.value = '{"agent_llm":"OpenAI","model_name":"fixture","api_key":"fixture-private-model-key"}'
        db.update(binding)
        move = ProjectActivity(project_id=board[2].id, card_id=card.id, user_id=actor.id,
            activity_type=ProjectActivityType.CardMoved,
            activity_history={"from_column": {"name": "Request"}, "column": {"name": "Doing"}})
        db.insert(move)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    service = DomainService().card_comment_compaction
    return service, actor, board[2], card, binding, comment, move, board[4]


def draft(snapshot):
    return CompactionDraft(claims=[{"text": "A change was reported, verification remains open", "kind": "unresolved",
                                   "evidence_refs": [source.evidence_ref for source in snapshot.sources]}])


async def test_server_collects_original_comments_and_moves_and_keeps_new_comments_visible(compact_command, monkeypatch):
    service, actor, project, card, binding, comment, move, _ = compact_command
    before = comment.model_dump(mode="json")
    async def generate(selected, snapshot):
        assert not DbSession.has_active_transaction()
        assert selected.id == binding.id
        assert {source.evidence_ref for source in snapshot.sources} == {f"comment:{comment.get_uid()}", f"activity:{move.get_uid()}"}
        assert "Original private comment" in snapshot.sources[0].text
        assert "Request" in snapshot.sources[1].text and "Doing" in snapshot.sources[1].text
        with DbSession.atomic() as db:
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Added during generation")))
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed" and result["has_more_sources"] is True
    assert "fixture-private-model-key" not in str(result)
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.id == comment.id)).first()
        comments = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.card_id == card.id)).all()
    assert current.model_dump(mode="json") == before and len(comments) == 2
    # An explicit next range reaches the new original comment, not the old range.
    next_snapshot, more = service._snapshot(project, card, after=result["range_end"])
    assert len(next_snapshot.sources) == 1 and not more
    assert "Added during generation" in next_snapshot.sources[0].text


@pytest.mark.parametrize("change", ["edit", "delete", "provider", "write_permission", "visibility"])
async def test_changes_during_generation_never_publish_stale_or_unauthorized_text(compact_command, monkeypatch, change):
    service, actor, project, card, binding, comment, _, role = compact_command
    async def generate(_, snapshot):
        with DbSession.atomic() as db:
            if change == "edit":
                comment.content = EditorContentModel(content="Edited during generation")
                db.update(comment)
            elif change == "delete":
                comment.deleted_at = SafeDateTime.now()
                db.update(comment)
            elif change == "provider":
                binding.value = '{"agent_llm":"OpenAI","model_name":"another"}'
                db.update(binding)
            elif change == "write_permission":
                role.actions = ["read"]
                db.update(role)
            else:
                card.visibility, card.owner_user_id = "PRIVATE", 2
                card.created_by_user_id = 2
                db.update(card)
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    if change == "visibility":
        with pytest.raises(CommentCompactionError) as denied:
            await service.generate(actor, project, card)
        assert denied.value.status == 404
    else:
        result = await service.generate(actor, project, card)
        assert result["status"] == "failed" and "draft" not in result
    with DbSession.use(readonly=False) as db:
        records = db.exec(SqlBuilder.select.table(CardCommentCompaction)).all()
    assert len(records) == 1 and records[0].draft is None


@pytest.mark.parametrize("change", ["edit", "delete", "provider", "read_permission", "policy"])
async def test_completed_summary_is_revalidated_on_every_read(compact_command, monkeypatch, change):
    service, actor, project, card, binding, comment, _, role = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed"
    with DbSession.atomic() as db:
        if change == "edit":
            comment.content = EditorContentModel(content="Edited after summary")
            db.update(comment)
        elif change == "delete":
            comment.deleted_at = SafeDateTime.now()
            db.update(comment)
        elif change == "provider":
            binding.is_default = False
            db.update(binding)
        elif change == "read_permission":
            role.actions = []
            db.update(role)
        else:
            record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
            record.policy_version += 1
            db.update(record)
    if change == "read_permission":
        with pytest.raises(CommentCompactionError) as denied:
            service.get(actor, project, card, result["uid"])
        assert denied.value.status == 403
    else:
        current = service.get(actor, project, card, result["uid"])
        assert current["status"] == "invalidated" and "draft" not in current


async def test_concurrent_command_calls_share_one_model_execution(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def generate(_, snapshot):
        calls.append(snapshot.source_revision())
        entered.set()
        await release.wait()
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    first = asyncio.create_task(service.generate(actor, project, card))
    await entered.wait()
    try:
        second = await service.generate(actor, project, card)
        assert second["status"] == "running" and "draft" not in second
        discovered = service.get_page(actor, project, card)
        assert discovered["records"][0]["uid"] == second["uid"] and discovered["records"][0]["status"] == "running"
    finally:
        release.set()
    result = await first
    assert result["uid"] == second["uid"] and result["status"] == "completed" and len(calls) == 1


async def test_explicit_cancellation_blocks_a_late_result(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    async def generate(_, snapshot):
        if model.await_count == 1:
            with DbSession.use(readonly=False) as db:
                record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
            assert service.cancel(actor, project, card, record.id)["status"] == "cancelled"
        return draft(snapshot)
    model = AsyncMock(side_effect=generate)
    monkeypatch.setattr(command, "request_compaction_draft", model)
    result = await service.generate(actor, project, card)
    assert result["status"] == "cancelled" and "draft" not in result
    assert (await service.generate(actor, project, card))["status"] == "cancelled"
    assert model.await_count == 1
    assert (await service.generate(actor, project, card, retry=True))["status"] == "completed"
    assert model.await_count == 2


async def test_task_cancellation_persists_a_terminal_state_and_propagates(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    entered = asyncio.Event()
    async def generate(*_):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    task = asyncio.create_task(service.generate(actor, project, card))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with DbSession.use(readonly=False) as db:
        record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
    assert record.status == "cancelled" and record.draft is None


async def test_expired_work_reports_explicit_retry_without_publishing_a_late_result(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    async def generate(_, snapshot):
        if model.await_count == 1:
            with DbSession.atomic() as db:
                record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
                record.lease_expires_at = SafeDateTime.now() - timedelta(seconds=1)
                db.update(record)
        return draft(snapshot)
    model = AsyncMock(side_effect=generate)
    monkeypatch.setattr(command, "request_compaction_draft", model)
    result = await service.generate(actor, project, card)
    assert result["status"] == "retry_required" and "draft" not in result
    assert (await service.generate(actor, project, card))["status"] == "retry_required" and model.await_count == 1
    assert (await service.generate(actor, project, card, retry=True))["status"] == "completed" and model.await_count == 2


@pytest.mark.parametrize("failure", ["provider", "invalid_result"])
async def test_model_failures_never_return_upstream_details_or_partial_text(compact_command, monkeypatch, failure):
    service, actor, project, card, *_ = compact_command
    error = RuntimeError("source and credential details") if failure == "provider" else CommentCompactionError("invalid_result")
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=error))
    result = await service.generate(actor, project, card)
    assert result["status"] == "failed" and "draft" not in result
    assert result["error_code"] == ("provider_error" if failure == "provider" else "invalid_result")
    assert "credential details" not in str(result)


def test_source_ranges_are_bounded_and_foreign_cursors_are_rejected(compact_command):
    service, actor, project, card, *_ = compact_command
    with DbSession.atomic() as db:
        for index in range(103):
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content=f"Comment {index}")))
        other = Card(project_id=project.id, project_column_id=card.project_column_id, title="Foreign source")
        db.insert(other)
        foreign = CardComment(card_id=other.id, user_id=actor.id, content=EditorContentModel(content="Outside"))
        db.insert(foreign)
    first, more = service._snapshot(project, card)
    second, remaining = service._snapshot(project, card, after=first.sources[-1].evidence_ref)
    assert len(first.sources) == 100 and more and len(second.sources) == 5 and not remaining
    assert len({source.evidence_ref for source in first.sources + second.sources}) == 105
    with pytest.raises(CommentCompactionError) as invalid:
        service._snapshot(project, card, after=f"comment:{foreign.get_uid()}")
    assert invalid.value.code == "invalid_cursor"


async def test_read_only_permission_cannot_start_model_work(compact_command, monkeypatch):
    service, actor, project, card, *_, role = compact_command
    with DbSession.atomic() as db:
        role.actions = ["read"]
        db.update(role)
    model = AsyncMock()
    monkeypatch.setattr(command, "request_compaction_draft", model)
    with pytest.raises(CommentCompactionError) as denied:
        await service.generate(actor, project, card)
    assert denied.value.status == 403 and model.await_count == 0
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(CardCommentCompaction)).all() == []


async def test_host_transaction_cannot_enclose_model_execution(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    model = AsyncMock()
    monkeypatch.setattr(command, "request_compaction_draft", model)
    with DbSession.atomic(), pytest.raises(CommentCompactionError) as denied:
        await service.generate(actor, project, card)
    assert denied.value.code == "transaction_not_supported" and model.await_count == 0


def test_compaction_pages_keep_sentinel_cursor_and_pending_jobs_inside_scope(compact_command):
    service, actor, project, card, binding, *_ = compact_command
    snapshot, _ = service._snapshot(project, card)
    expected = set()
    for index in range(6):
        version = snapshot.model_copy(deep=True)
        version.sources[0].text += f" version {index}"
        expected.add(service.repo.card_comment_compaction.reserve(card, actor, binding, version).get_uid())
    with DbSession.atomic() as db:
        other = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
        foreign = Card(project_id=project.id, project_column_id=card.project_column_id, title="Foreign requests")
        db.insert(foreign)
    hidden = service.repo.card_comment_compaction.reserve(card, other, binding, snapshot)
    foreign_request = service.repo.card_comment_compaction.reserve(foreign, actor, binding, snapshot)
    first = service.get_page(actor, project, card)
    assert len(first["records"]) == 5 and first["next_cursor"]
    second = service.get_page(actor, project, card, before=first["next_cursor"])
    assert len(second["records"]) == 1 and second["next_cursor"] is None
    assert {row["uid"] for row in first["records"] + second["records"]} == expected
    for record in (hidden, foreign_request):
        with pytest.raises(CommentCompactionError) as denied:
            service.get_page(actor, project, card, before=record.get_uid())
        assert denied.value.code == "invalid_cursor"


def test_authenticated_http_uses_native_graph_service_auth_and_rejects_client_sources(compact_command, monkeypatch):
    _, actor, project, card, *_ = compact_command
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "local-compaction-http-fixture-secret-32bytes"))
    actual_client = httpx.AsyncClient
    requests = []
    def graph(request):
        token = request.headers["authorization"].removeprefix("Bearer ")
        assert verify_graph_service_token(token)
        body = json.loads(request.content)
        snapshot = CommentCompactionSnapshot(sources=body["sources"])
        requests.append(snapshot)
        return httpx.Response(200, json={"generated": True, "status": "draft",
            "source_revision": snapshot.source_revision(), "draft": draft(snapshot).model_dump(mode="json")})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(graph), **kwargs))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    endpoint = f"/board/{project.get_uid()}/card/{card.get_uid()}/comment-compactions"
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as client:
        assert client.post(endpoint, json={}).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        rejected = client.post(endpoint, json={"sources": ["client supplied"]}, headers=headers)
        assert rejected.status_code == 400, rejected.text
        assert requests == []
        result = client.post(endpoint, json={}, headers=headers)
        assert result.status_code == 200, result.text
        assert result.headers["Cache-Control"] == "no-store" and result.json()["status"] == "completed"
        assert len(requests) == 1 and "fixture-private-model-key" not in result.text
        assert client.get(endpoint + "/" + result.json()["uid"], headers=headers).json()["status"] == "completed"
        listed = client.get(endpoint, headers=headers)
        assert listed.status_code == 200 and listed.headers["Cache-Control"] == "no-store"
        assert listed.json()["records"][0]["uid"] == result.json()["uid"] and len(requests) == 1
        foreign = Card(project_id=project.id, project_column_id=card.project_column_id, title="Foreign card")
        with DbSession.atomic() as db:
            db.insert(foreign)
        path = f"/board/{project.get_uid()}/card/{foreign.get_uid()}/comment-compactions/{result.json()['uid']}"
        assert client.get(path, headers=headers).status_code == 404
