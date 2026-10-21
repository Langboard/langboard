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
from langboard.routes.activities import ActivityApi  # noqa: F401
from langboard.routes.board import BoardCardCommentCompactionApi  # noqa: F401
from langboard_shared.ai.comment_compaction import CommentCompactionSnapshot, CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.security.GraphServiceAuth import verify_graph_service_token
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardComment,
    CardCommentCompaction,
    CardCommentReaction,
    InternalBot,
    Project,
    ProjectActivity,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardCommentCompactionService import CommentCompactionError
from langboard_shared.Env import Env
from sqlalchemy import delete, update


command = importlib.import_module("langboard_shared.domain.services.factory.CardCommentCompactionService")


@pytest.fixture
def compact_command(compactions, board, monkeypatch):
    repo, actor, card, binding, comment, *_ = compactions
    ProjectActivity.__table__.create(DbEngine.get_main_engine())
    CardCommentReaction.__table__.create(DbEngine.get_main_engine())
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
                                   "evidence_refs": [source.evidence_ref for source in snapshot.sources[:20]]}])


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
    oldest = second["records"][0]
    recovered = service.get_page(actor, project, card, request_key_filter=oldest["request_key"])
    assert [row["uid"] for row in recovered["records"]] == [oldest["uid"]]
    assert recovered["next_cursor"] is None
    assert service.get_page(actor, project, card, request_key_filter=hidden.request_key)["records"] == []
    assert service.get_page(actor, project, card, request_key_filter=foreign_request.request_key)["records"] == []
    with pytest.raises(CommentCompactionError) as invalid:
        service.get_page(actor, project, card, request_key_filter="invalid")
    assert invalid.value.code == "invalid_request_key"
    for record in (hidden, foreign_request):
        with pytest.raises(CommentCompactionError) as denied:
            service.get_page(actor, project, card, before=record.get_uid())
        assert denied.value.code == "invalid_cursor"


async def test_eligibility_uses_server_volume_and_long_comment_sources(compact_command, monkeypatch):
    service, actor, project, card, _, comment, _, role = compact_command
    small = service.get_page(actor, project, card)["eligibility"]
    assert small["event_count"] == 2 and small["provider_configured"] and small["can_generate"]
    assert not small["needs_compaction"]
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="Long evidence " * 2500)
        db.update(comment)
    large = service.get_page(actor, project, card)["eligibility"]
    assert large["source_characters"] > 30_000 and large["needs_compaction"]
    async def generate(_, snapshot):
        assert len(snapshot.sources[0].text) > 30_000
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed" and "covered_through" in result
    with DbSession.atomic() as db:
        role.actions = ["read"]
        db.update(role)
    assert service.get_page(actor, project, card)["eligibility"]["can_generate"] is False


async def test_explicit_original_evidence_reuses_bounded_timeline_and_excludes_new_comments(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    with DbSession.atomic() as db:
        for index in range(40):
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content=f"Covered {index}")))
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    result = await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        later = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Later original"))
        db.insert(later)
    seen, sizes, before = [], [], None
    while True:
        page = service.get_evidence(actor, project, card, result["uid"], before=before)
        sizes.append(len(page["records"]))
        seen.extend(row["evidence_ref"] for row in page["records"])
        before = page["next_cursor"]
        if before is None:
            break
    assert sizes == [20, 20, 2] and len(seen) == len(set(seen)) == 42
    assert set(seen) == set(result["evidence_refs"])
    assert f"comment:{later.get_uid()}" not in seen
    with pytest.raises(CommentCompactionError) as invalid:
        service.get_evidence(actor, project, card, result["uid"], before=f"comment:{later.get_uid()}")
    assert invalid.value.code == "invalid_cursor"


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
        recovered = client.get(endpoint, params={"request_key": result.json()["request_key"]}, headers=headers)
        assert recovered.status_code == 200 and [row["uid"] for row in recovered.json()["records"]] == [result.json()["uid"]]
        assert client.get(endpoint, params={"request_key": "invalid"}, headers=headers).status_code == 400
        evidence = client.get(endpoint + "/" + result.json()["uid"] + "/evidence", headers=headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.headers["Cache-Control"] == "no-store" and len(evidence.json()["records"]) == 2
        view = client.get(f"/activity/project/{project.get_uid()}/card/{card.get_uid()}/timeline?compact=true", headers=headers)
        assert view.status_code == 200, view.text
        assert view.headers["Cache-Control"] == "no-store"
        assert [entry["kind"] for entry in view.json()["records"]] == ["compaction"]
        assert "Original private comment" not in view.text
        foreign = Card(project_id=project.id, project_column_id=card.project_column_id, title="Foreign card")
        with DbSession.atomic() as db:
            db.insert(foreign)
        path = f"/board/{project.get_uid()}/card/{foreign.get_uid()}/comment-compactions/{result.json()['uid']}"
        assert client.get(path, headers=headers).status_code == 404


@pytest.mark.parametrize("change", ["card", "project", "user", "role", "provider", "visibility", "project_owner"])
async def test_current_primary_rechecks_survive_identity_map_cache(compact_command, monkeypatch, change):
    service, actor, project, card, binding, _, _, role = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    result = await service.generate(actor, project, card)
    model, record_id, field, value = {
        "card": (Card, card.id, "deleted_at", SafeDateTime.now()),
        "project": (Project, project.id, "deleted_at", SafeDateTime.now()),
        "user": (User, actor.id, "activated_at", None),
        "role": (ProjectRole, role.id, "actions", []),
        "provider": (InternalBot, binding.id, "value", '{"agent_llm":"OpenAI","model_name":"new configuration"}'),
        "visibility": (Card, card.id, "visibility", "PRIVATE"),
        "project_owner": (Project, project.id, "owner_id", actor.id),
    }[change]
    with DbSession.atomic() as db:
        held = db.exec(SqlBuilder.select.table(model).where(model.id == record_id)).first()
        old = getattr(held, field)
        assert service.get(actor, project, card, result["uid"])["status"] == "completed"
        values = {field: value}
        if change == "visibility":
            values.update(owner_user_id=2, created_by_user_id=2)
        if change == "project_owner":
            from langboard_shared.domain.models import ProjectAssignedUser
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == actor.id))
        db.exec(update(model).execution_options(synchronize_session=False).where(model.id == record_id).values(**values))
        ordinary = db.exec(SqlBuilder.select.table(model).where(model.id == record_id)).first()
        if change in {"card", "project"}:
            assert ordinary is None  # Existing soft-delete predicates already fail closed.
        else:
            assert ordinary is held and getattr(ordinary, field) == old
        if change == "project_owner":
            assert service.get(actor, project, card, result["uid"])["status"] == "completed"
        elif change == "provider":
            reread = service.get(actor, project, card, result["uid"])
            assert reread["status"] == "invalidated" and "draft" not in reread
        else:
            with pytest.raises(CommentCompactionError) as denied:
                service.get_evidence(actor, project, card, result["uid"])
            assert denied.value.status == (403 if change == "role" else 404)


async def test_server_request_key_advances_uncovered_ranges_and_replays_without_duplicate_model_calls(compact_command, monkeypatch):
    service, actor, project, card, _, comment, *_ = compact_command
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="First long evidence " * 1000)
        db.update(comment)
    snapshots = []
    async def generate(_, snapshot):
        snapshots.append(snapshot)
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    key = service.get_page(actor, project, card)["eligibility"]["next_request_key"]
    assert key
    first = await service.generate(actor, project, card, expected_request_key=key)
    assert (await service.generate(actor, project, card, expected_request_key=key))["uid"] == first["uid"]
    assert len(snapshots) == 1 and not service.get_page(actor, project, card)["eligibility"]["needs_compaction"]
    with DbSession.atomic() as db:
        later = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="New long evidence " * 1000))
        db.insert(later)
    next_key = service.get_page(actor, project, card)["eligibility"]["next_request_key"]
    assert next_key and next_key != key
    second = await service.generate(actor, project, card, expected_request_key=next_key)
    assert second["uid"] != first["uid"] and len(snapshots) == 2
    assert [source.evidence_ref for source in snapshots[1].sources] == [f"comment:{later.get_uid()}"]
    assert (await service.generate(actor, project, card, expected_request_key=key))["uid"] == first["uid"] and len(snapshots) == 2


async def test_stale_preview_key_cannot_silently_create_another_request(compact_command, monkeypatch):
    service, actor, project, card, _, comment, *_ = compact_command
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="Previewed evidence " * 1000)
        db.update(comment)
    key = service.get_page(actor, project, card)["eligibility"]["next_request_key"]
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="Changed evidence " * 1000)
        db.update(comment)
    model = AsyncMock()
    monkeypatch.setattr(command, "request_compaction_draft", model)
    with pytest.raises(CommentCompactionError) as stale:
        await service.generate(actor, project, card, expected_request_key=key)
    assert stale.value.code == "source_changed" and model.await_count == 0
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(CardCommentCompaction)).all() == []


async def test_default_view_returns_blocks_instead_of_covered_originals_and_keeps_new_comments(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    summary = await service.generate(actor, project, card)
    view = service.get_view(actor, project, card)
    assert [record["kind"] for record in view["records"]] == ["compaction"]
    assert view["records"][0]["uid"] == summary["uid"] and view["next_cursor"] is None
    assert "Original private comment" not in json.dumps(view)
    assert not view["eligibility"]["needs_compaction"]
    with DbSession.atomic() as db:
        later = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Current uncovered comment"))
        db.insert(later)
    current = service.get_view(actor, project, card)
    assert [record["kind"] for record in current["records"]] == ["comment", "compaction"]
    assert current["records"][0]["uid"] == later.get_uid()
    assert current["eligibility"]["uncompacted_event_count"] == 1
    assert "fixture-private-model-key" not in json.dumps(current, default=str)


async def test_compact_view_cursor_crosses_block_boundaries_without_refetching_covered_comments(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    summary = await service.generate(actor, project, card)
    expected = {f"compaction:{summary['uid']}"}
    with DbSession.atomic() as db:
        for index in range(25):
            comment = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content=f"New comment {index}"))
            db.insert(comment)
            expected.add(f"comment:{comment.get_uid()}")
    records, sizes, before = [], [], None
    while True:
        page = service.get_view(actor, project, card, before=before)
        sizes.append(len(page["records"]))
        records.extend(record["evidence_ref"] for record in page["records"])
        before = page["next_cursor"]
        if before is None:
            break
    assert sizes == [20, 6] and len(records) == len(set(records))
    assert set(records) == expected


def test_view_recovers_own_pending_request_even_when_newer_completed_records_fill_overview(compact_command):
    service, actor, project, card, binding, *_ = compact_command
    snapshot, _ = service._snapshot(project, card)
    pending = service.repo.card_comment_compaction.reserve(card, actor, binding, snapshot)
    with DbSession.atomic() as db:
        other = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
    for index in range(6):
        old_snapshot = snapshot.model_copy(deep=True)
        old_snapshot.sources[0].text += f" previous version {index}"
        historical = service.repo.card_comment_compaction.reserve(card, other, binding, old_snapshot)
        with DbSession.atomic() as db:
            historical.status = "completed"
            historical.draft = draft(old_snapshot).model_dump(mode="json")
            db.update(historical)
    assert all(row["uid"] != pending.get_uid() for row in service.get_page(actor, project, card)["records"])
    current = service.get_view(actor, project, card)
    assert [row["uid"] for row in current["in_flight"]] == [pending.get_uid()]
    assert current["in_flight"][0]["request_key"] == pending.request_key


async def test_real_mcp_compact_defaults_and_scoped_original_fragments(compact_command, monkeypatch):
    from langboard.mcp_tools import CardMcp
    assert CardMcp.get_card_timeline and CardMcp.get_card_bundle
    from types import SimpleNamespace
    from fastmcp import Client
    from langboard.card_workspace.domain import CommentCursor
    from langboard.mcp_integration.Server import McpServer
    from langboard.mcp_integration.Tool import McpTool
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
    from langboard_shared.core.db import BaseDbModel
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel

    service, actor, project, card, binding, comment, move, _ = compact_command
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    from langboard_shared.domain.models import CardExecutionGeneration
    BaseDbModel.metadata.create_all(DbEngine.get_main_engine())
    CardExecutionGeneration.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    long_text = "Original private comment " + "long source evidence " * 1100
    with DbSession.atomic() as db:
        card.visibility, card.owner_user_id, card.created_by_user_id = "PRIVATE", actor.id, actor.id
        db.update(card)
        comment.content = EditorContentModel(content=long_text)
        db.update(comment)
    model = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", model)
    summary = await service.generate(actor, project, card, channel=CollaborationChannel.Mcp)
    names = ("get_card_timeline", "get_card_bundle")
    metadata = {name: McpTool.get_tool(name) for name in names}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda key: metadata.get(key))
    _, server = McpServer.get_http_app("agent")
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=list(names)), "user_or_bot": actor})
    arguments = {"project_uid": project.get_uid(), "card_uid": card.get_uid()}
    try:
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert "compaction_uid" in tools["get_card_timeline"].inputSchema["properties"]
            default = await client.call_tool("get_card_timeline", arguments)
            assert not default.is_error, default
            assert [item["kind"] for item in default.data["records"]] == ["compaction"]
            text = json.dumps(default.data, default=str)
            assert long_text[:100] not in text and "fixture-private-model-key" not in text
            assert "request_key" not in text and "provider_revision" not in text
            assert f"comment:{comment.get_uid()}" in text and f"activity:{move.get_uid()}" in text
            bundle = await client.call_tool("get_card_bundle", {**arguments, "include": ["comments"]})
            assert not bundle.is_error, bundle
            data = bundle.structured_content
            assert [item["kind"] for item in data["card"]["comments"]["items"]] == ["compaction"]
            originals = await client.call_tool("get_card_timeline", {**arguments, "compaction_uid": summary["uid"]})
            assert {item["kind"] for item in originals.data["records"]} == {"comment", "activity"}
            source_args = {**arguments, "compaction_uid": summary["uid"], "evidence_ref": f"comment:{comment.get_uid()}"}
            fragments = []
            cursor = None
            while True:
                fragment = await client.call_tool("get_card_timeline", {**source_args, **({"content_cursor": cursor} if cursor else {})})
                assert not fragment.is_error, fragment
                source = fragment.data["source"]
                assert len(source["content"]) <= 8000
                fragments.append(source["content"])
                cursor = source["next_cursor"]
                if not cursor:
                    break
            assert len(fragments) >= 3
            recovered = json.loads("".join(fragments))
            assert recovered["comment"]["content"]["content"] == long_text
            assert "fixture-private-model-key" not in str(recovered)
            first_fragment = await client.call_tool("get_card_timeline", source_args)
            cursor = first_fragment.data["source"]["next_cursor"]
            foreign = await client.call_tool("get_card_timeline", {**source_args, "evidence_ref": "comment:foreign"}, raise_on_error=False)
            assert foreign.is_error and long_text[:100] not in str(foreign.content)
            cross_source = await client.call_tool("get_card_timeline", {**source_args, "evidence_ref": f"activity:{move.get_uid()}", "content_cursor": cursor}, raise_on_error=False)
            assert cross_source.is_error
            with DbSession.atomic() as db:
                new_comments = [CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content=f"Uncovered {i}")) for i in range(6)]
                for row in new_comments:
                    db.insert(row)
            from langboard.card_workspace.application import get_card_bundle
            from langboard.card_workspace.domain import CommentPage, SectionPage
            from langboard.card_workspace.infrastructure.native import NativeCardWorkspaceAdapter
            adapter = NativeCardWorkspaceAdapter(actor, DomainService(), channel=CollaborationChannel.Mcp)
            seen = []
            after = None
            while True:
                result = get_card_bundle(adapter, project.get_uid(), card.get_uid(), CommentPage(limit=2, cursor=after), SectionPage(), include=["comments"])
                page = result.continuation.page if after else result.card.comments
                seen.extend(page.items)
                after = page.next_cursor
                if not after:
                    break
            assert len(seen) == 7 and len({(item["kind"], item["uid"]) for item in seen}) == 7
            assert seen[-1]["kind"] == "compaction" and all(item["uid"] != comment.get_uid() for item in seen)
            legacy = CommentCursor(created_at=new_comments[-1].created_at.isoformat(), comment_uid=new_comments[-1].get_uid()).encode()
            old_page = get_card_bundle(adapter, project.get_uid(), card.get_uid(), CommentPage(limit=20, cursor=legacy), SectionPage(), include=["comments"])
            assert all(item["uid"] != comment.get_uid() for item in old_page.continuation.page.items)
            with DbSession.atomic() as db:
                comment.content = EditorContentModel(content="Changed original")
                db.update(comment)
            stale = await client.call_tool("get_card_timeline", {**source_args, "content_cursor": cursor}, raise_on_error=False)
            assert stale.is_error and long_text[:100] not in str(stale.content)
            current = await client.call_tool("get_card_timeline", arguments)
            assert not current.is_error and all(item["kind"] != "compaction" for item in current.data["records"])
            with DbSession.atomic() as db:
                card.owner_user_id, card.created_by_user_id = 2, 2
                db.update(card)
            denied = await client.call_tool("get_card_timeline", source_args, raise_on_error=False)
            assert denied.is_error and "Changed original" not in str(denied.content)
            assert model.await_count == 1
    finally:
        mcp_auth_context.reset(token)
