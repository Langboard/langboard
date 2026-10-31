"""The native command collects its own primary evidence and rechecks every output."""

import asyncio
import importlib
import json
from datetime import datetime, timedelta
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
    CardMetadata,
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
from sqlalchemy import delete, event, update
from src.api.tests.model_providers.workflow_rule_fixtures import configure_rule, create_rule_tables, rule_id


command = importlib.import_module("langboard_shared.domain.services.factory.CardCommentCompactionService")


def test_compaction_snapshot_includes_anchor_and_detects_location_only_change(compact_command):
    service, actor, project, card, binding, comment, *_ = compact_command
    original, _ = service._snapshot(project, card)
    source_ref = f"comment:{comment.get_uid()}"
    initial_source = next(source for source in original.sources if source.evidence_ref == source_ref)
    assert "anchor" not in json.loads(initial_source.text)
    anchor = {"type": "TextQuoteSelector", "version": 1, "exact": "Original private comment"}
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(anchor=anchor, updated_at=comment.updated_at))
    snapshot, _ = service._snapshot(project, card)
    located = next(source for source in snapshot.sources if source.evidence_ref == source_ref)
    assert json.loads(located.text)["anchor"] == anchor
    assert located.updated_at == initial_source.updated_at
    assert snapshot.source_revision() != original.source_revision()
    repository = service.repo.card_comment_compaction
    record = repository.claim(repository.reserve(card, actor, binding, snapshot))
    assert repository.complete(record, record.attempt, snapshot, binding, draft(snapshot))
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(
            anchor={**anchor, "exact": "Different location"}, updated_at=comment.updated_at,
        ))
    assert service.get(actor, project, card, record.id)["status"] == "invalidated"
    with DbSession.use(readonly=False) as db:
        saved = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.id == comment.id)).first()
    assert saved.anchor["exact"] == "Different location" and saved.content == comment.content


def test_profile_owned_summary_reuses_authorized_read_view_without_user_ownership(compact_command):
    service, actor, project, card, binding, *_ = compact_command
    snapshot, _ = service._snapshot(project, card)
    repository = service.repo.card_comment_compaction
    record = repository.reserve_automatic(card, binding, snapshot)
    # A human reader does not own the compactor's pending request or cancellation.
    with pytest.raises(CommentCompactionError) as denied:
        service.get(actor, project, card, record.id)
    assert denied.value.status == 404
    with pytest.raises(CommentCompactionError):
        service.cancel(actor, project, card, record.id)
    claimed = repository.claim(record)
    assert repository.complete(record, claimed.attempt, snapshot, binding, draft(snapshot))
    block = service.get(actor, project, card, record.id)
    assert block["status"] == "completed" and "request_key" not in block
    assert block["generated_by"] == {"uid": binding.get_uid(), "display_name": binding.display_name}
    view = service.get_view(actor, project, card)
    assert [entry["kind"] for entry in view["records"]] == ["compaction"]
    assert "Original private comment" not in json.dumps(view)


@pytest.fixture
def compact_command(compactions, board, monkeypatch):
    repo, actor, card, binding, comment, *_ = compactions
    create_rule_tables()
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


@pytest.fixture
def automatic_command(compact_command):
    from langboard_shared.domain.models import ProjectColumn, WorkflowStageDefinition
    from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
    from langboard_shared.domain.services.CompactorBoardAuthority import set_compactor_board_grant

    service, actor, project, card, binding, *_ = compact_command
    CompactorBoardGrant.__table__.create(DbEngine.get_main_engine())
    with DbSession.atomic() as db:
        project.owner_id = actor.id
        db.update(project)
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        column.workflow_stage = "active"
        db.update(column)
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == "active")).first()
        configure_rule(db, stage)
        card.workflow_stage_entered_key = stage.key
        card.workflow_stage_entered_at = SafeDateTime.now() - timedelta(hours=2)
        db.update(card)
        for index in range(18):
            db.insert(CardComment(card_id=card.id, user_id=actor.id,
                                  content=EditorContentModel(content=f"Additional evidence {index} " + "x" * 60)))
    set_compactor_board_grant(actor, project.id, binding.id, can_read=True, can_compact=True, can_archive=False)
    return compact_command, column, stage


async def test_automatic_command_uses_profile_and_reuses_valid_coverage(automatic_command, monkeypatch):
    from langboard_shared.domain.models import WorkflowEffectRun

    (service, actor, project, card, binding, *_), _, _ = automatic_command
    generate = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    receipt = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    assert receipt["status"] == "completed" and "draft" not in receipt
    assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}
    assert generate.await_count == 1
    block = service.get(actor, project, card, receipt["uid"])
    assert block["status"] == "completed" and len(block["evidence_refs"]) == 20
    with DbSession.use(readonly=False) as db:
        record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
        run = db.exec(SqlBuilder.select.table(WorkflowEffectRun)).first()
    assert record.user_id is None and record.internal_bot_id == binding.id
    assert (run.actor_type, run.actor_id, run.source, run.status) == ("profile", binding.id, "continuous", "succeeded")


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_automatic_receipt_rechecks_actual_uncovered_sources_after_inference(automatic_command, monkeypatch, outcome):
    (service, actor, project, card, binding, *_), _, stage = automatic_command

    async def generate(_, snapshot):
        with DbSession.atomic() as db:
            db.insert(CardComment(card_id=card.id, user_id=actor.id,
                                  content=EditorContentModel(content="New evidence during automatic inference")))
            if outcome == "cancelled":
                record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
                assert service.repo.card_comment_compaction.cancel(record)
        if outcome == "failed":
            raise RuntimeError("Controlled provider failure")
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    receipt = await service.generate_automatic(project, binding, card, rule_id=rule_id(stage))
    assert receipt["status"] == outcome and receipt["has_more_sources"] is True
    if outcome == "completed":
        assert service.get(actor, project, card, receipt["uid"])["has_more_sources"] is True
    else:
        with DbSession.use(readonly=False) as db:
            record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
        assert record.draft is None


@pytest.mark.parametrize("mutation", ["revoke", "policy", "inactive", "column", "archive", "source", "provider"])
async def test_automatic_command_rechecks_authority_policy_and_sources(automatic_command, monkeypatch, mutation):
    from langboard_shared.domain.services.CompactorBoardAuthority import set_compactor_board_grant

    (service, actor, project, card, binding, comment, *_), column, stage = automatic_command

    async def generate(_, snapshot):
        assert not DbSession.has_active_transaction()
        if mutation == "revoke":
            set_compactor_board_grant(actor, project.id, binding.id, can_read=False, can_compact=False, can_archive=False)
        else:
            with DbSession.atomic() as db:
                if mutation == "policy":
                    configure_rule(db, stage, enabled=False)
                    db.update(stage)
                elif mutation == "inactive":
                    stage.is_active = False
                    db.update(stage)
                elif mutation == "column":
                    column.workflow_stage = None
                    db.update(column)
                elif mutation == "archive":
                    column.is_archive = True
                    db.update(column)
                elif mutation == "source":
                    comment.content = EditorContentModel(content="Changed source")
                    db.update(comment)
                else:
                    binding.is_default = False
                    db.update(binding)
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    receipt = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    expected = "source_changed" if mutation == "source" else "provider_changed" if mutation == "provider" else "permission_changed"
    assert receipt["status"] == "failed" and receipt["error_code"] == expected
    assert receipt["has_more_sources"] is (True if mutation == "source" else None)
    with DbSession.use(readonly=False) as db:
        record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
    assert record.draft is None


async def test_automatic_command_requires_grant_before_sending_sources(automatic_command, monkeypatch):
    from langboard_shared.domain.services.CompactorBoardAuthority import set_compactor_board_grant

    (service, actor, project, card, binding, *_), _, _ = automatic_command
    set_compactor_board_grant(actor, project.id, binding.id, can_read=True, can_compact=False, can_archive=True)
    generate = AsyncMock()
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    with pytest.raises(CommentCompactionError) as denied:
        await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    assert denied.value.status == 403
    generate.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["private", "policy", "inactive", "archive", "card_archive"])
async def test_automatic_command_rejects_ineligible_cards_before_provider(automatic_command, monkeypatch, mutation):
    (service, actor, project, card, binding, *_), column, stage = automatic_command
    with DbSession.atomic() as db:
        if mutation == "private":
            # Visibility is immutable in the model, so create a private source rather than bypass it.
            card = Card(project_id=project.id, project_column_id=column.id, title="Private", visibility="PRIVATE",
                        owner_user_id=actor.id, created_by_user_id=actor.id)
            db.insert(card)
        elif mutation == "policy":
            configure_rule(db, stage, enabled=False)
            db.update(stage)
        elif mutation == "inactive":
            stage.is_active = False
            db.update(stage)
        elif mutation == "card_archive":
            card.archived_at = SafeDateTime.now()
            db.update(card)
        else:
            column.is_archive = True
            db.update(column)
    generate = AsyncMock()
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    with pytest.raises(CommentCompactionError):
        await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    generate.assert_not_awaited()


async def test_automatic_command_coalesces_concurrent_attempts_and_keeps_provider_outside_transaction(automatic_command, monkeypatch):
    (service, _, project, card, binding, *_), _, _ = automatic_command
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(_, snapshot):
        assert not DbSession.has_active_transaction()
        started.set()
        await release.wait()
        return draft(snapshot)

    provider = AsyncMock(side_effect=generate)
    monkeypatch.setattr(command, "request_compaction_draft", provider)
    first = asyncio.create_task(service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
        assert second["status"] == "running"
    finally:
        release.set()
    completed = await first
    assert completed["uid"] == second["uid"] and completed["status"] == "completed"
    assert provider.await_count == 1


@pytest.fixture
def scheduled_command(automatic_command):
    return automatic_command


def common_runs():
    from langboard_shared.domain.models import WorkflowActionRun, WorkflowRuleRun
    with DbSession.use(readonly=False) as db:
        return (db.exec(SqlBuilder.select.table(WorkflowRuleRun).order_by(WorkflowRuleRun.id)).all(),
                db.exec(SqlBuilder.select.table(WorkflowActionRun).order_by(WorkflowActionRun.id)).all())


async def test_common_rule_worker_keeps_interval_and_skips_already_covered_sources(scheduled_command, monkeypatch):
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    (service, actor, project, card, binding, *_), _, _ = scheduled_command
    provider = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", provider)
    now = SafeDateTime.now()
    assert await run_due_rules(now=now) == 1
    assert await run_due_rules(now=now + timedelta(seconds=59)) == 0
    runs, claims = common_runs()
    assert runs[0].status == "succeeded"
    assert claims[0].status == "succeeded" and claims[0].lease_expires_at is None
    assert claims[0].executor_id == binding.id and claims[0].attempt == 1
    assert await run_due_rules(now=now + timedelta(seconds=301)) == 1
    assert common_runs()[0][-1].status == "skipped"
    assert provider.await_count == 1
    assert service.get_view(actor, project, card)["records"][0]["kind"] == "compaction"


async def test_common_rule_worker_fences_ambiguous_provider_failure_across_intervals(scheduled_command, monkeypatch):
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    calls = 0

    async def provider(_, snapshot):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Temporary provider failure")
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", provider)
    now = SafeDateTime.now()
    assert await run_due_rules(now=now) == 1
    assert await run_due_rules(now=now + timedelta(seconds=59)) == 0
    runs, claims = common_runs()
    assert runs[0].status == "uncertain" and claims[0].status == "uncertain"
    assert claims[0].lease_expires_at is None and claims[0].attempt == 1
    assert await run_due_rules(now=now + timedelta(seconds=301)) == 0
    with DbSession.use(readonly=False) as db:
        record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
    assert calls == 1 and record.status == "failed" and record.attempt == 1 and record.user_id is None
    assert len(common_runs()[0]) == 1


async def test_common_rule_claim_capacity_recovery_and_late_finish_fencing(scheduled_command, monkeypatch):
    from langboard_shared.domain.models import WorkflowActionRun
    from langboard_shared.tasks import WorkflowRuleTask as worker

    (service, _, project, card, binding, *_), column, stage = scheduled_command
    now = SafeDateTime.now()
    worker._admit_intervals(now, 5)
    first_run = common_runs()[0][0]
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(_, snapshot):
        assert not DbSession.has_active_transaction()
        started.set()
        await release.wait()
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", provider)
    first = asyncio.create_task(worker.run_rule(first_run.id))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await worker.run_rule(first_run.id)
        claims = common_runs()[1]
        assert len(claims) == 1 and claims[0].status == "running" and claims[0].attempt == 1
        with DbSession.atomic() as db:
            for title in ("Second", "Third"):
                db.insert(Card(project_id=project.id, project_column_id=column.id, title=title,
                    workflow_stage_entered_key=stage.key, workflow_stage_entered_at=card.workflow_stage_entered_at))
        worker._admit_intervals(now, 5)
        with DbSession.atomic() as db:
            # A separately admitted card consumes the remaining bounded slot.
            second = db.exec(SqlBuilder.select.table(WorkflowActionRun).where(
                WorkflowActionRun.id != claims[0].id).order_by(WorkflowActionRun.id)).first()
            second.status, second.attempt, second.executor_id = "running", 1, binding.id
            second.lease_expires_at = now + timedelta(hours=1)
            db.update(second)
        assert await worker.run_due_rules(now=now) == 0
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(WorkflowActionRun).where(WorkflowActionRun.id == claims[0].id)).first()
            current.lease_expires_at = now - timedelta(seconds=1)
            db.update(current)
        await worker.run_rule(first_run.id)
        fenced = next(item for item in common_runs()[1] if item.id == claims[0].id)
        assert fenced.status == "uncertain" and fenced.error_code == "lease_expired" and fenced.attempt == 1
    finally:
        release.set()
        await first
    fenced = next(item for item in common_runs()[1] if item.id == claims[0].id)
    assert fenced.status == "uncertain" and fenced.error_code == "lease_expired"


async def test_common_rule_postgres_concurrent_claim_has_one_owner(scheduled_command, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from langboard_shared.tasks import WorkflowRuleTask as worker

    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("Concurrent FOR UPDATE requires PostgreSQL")
    worker._admit_intervals(SafeDateTime.now(), 5)
    run = common_runs()[0][0]
    barrier = Barrier(2)
    provider = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", provider)

    def execute():
        barrier.wait(timeout=5)
        return asyncio.run(worker.run_rule(run.id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    claims = common_runs()[1]
    assert len(claims) == 1 and claims[0].attempt == 1 and claims[0].status == "succeeded"
    assert claims[0].lease_expires_at is None and provider.await_count == 1


async def test_common_rule_scan_rechecks_revoked_grants_before_claim(scheduled_command, monkeypatch):
    from langboard_shared.domain.services.CompactorBoardAuthority import set_compactor_board_grant
    from langboard_shared.tasks import WorkflowRuleTask as worker

    (_, actor, project, _, binding, *_), _, _ = scheduled_command
    original = worker._admit_intervals

    def admit(*args):
        count = original(*args)
        set_compactor_board_grant(actor, project.id, binding.id, can_read=False, can_compact=False, can_archive=False)
        return count

    monkeypatch.setattr(worker, "_admit_intervals", admit)
    provider = AsyncMock()
    monkeypatch.setattr(command, "request_compaction_draft", provider)
    assert await worker.run_due_rules() == 1
    assert common_runs()[0][0].status == "skipped"
    assert common_runs()[1][0].attempt == 0
    provider.assert_not_awaited()


async def test_compaction_persists_evidence_bound_card_search_metadata_and_expands_search(compact_command, monkeypatch):
    from langboard_shared.domain.models import CardAttachment

    service, actor, project, card, binding, comment, *_ = compact_command
    CardAttachment.__table__.create(DbEngine.get_main_engine())

    async def provider(_, snapshot):
        return CompactionDraft(claims=draft(snapshot).claims,
            keywords=[{"value": "동시편집", "evidence_refs": [snapshot.sources[0].evidence_ref]}],
            entities=[{"name": "Phoenix", "kind": "technology", "aliases": ["Elixir Phoenix"],
                       "evidence_refs": [snapshot.sources[0].evidence_ref]}])

    monkeypatch.setattr(command, "request_compaction_draft", provider)
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="동시편집 Phoenix Elixir Phoenix 문제")
        db.update(comment)
    receipt = await service.generate(actor, project, card)
    with DbSession.use(readonly=False) as db:
        stored = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
    assert stored.draft["entities"][0]["name"] == "Phoenix"
    with DbSession.use(readonly=False) as db:
        row = db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.card_id == card.id)).first()
    assert row.key == f"internal.comment_compaction.{receipt['uid']}.search"
    assert json.loads(row.value)["entities"][0]["name"] == "Phoenix"
    assert DomainService().metadata.get_all_as_api(CardMetadata, card) == []
    assert DomainService().metadata.get_by_key_as_api(CardMetadata, card, row.key) is None
    with DbSession.atomic() as db:
        db.insert(CardMetadata(card_id=card.id, key="z-public", value="manual"))
    assert DomainService().metadata.get_all_as_api(CardMetadata, card, as_dict=True, limit=1) == {"z-public": "manual"}
    with pytest.raises(ValueError, match="server-owned"):
        DomainService().metadata.save(CardMetadata, card, row.key, "forged")
    with pytest.raises(ValueError, match="server-owned"):
        DomainService().metadata.delete(CardMetadata, card, row.key)
    metadata = service.get_search_metadata(actor, project, card)
    assert metadata["source"] == "comment_compaction" and metadata["derived"] is True
    assert metadata["keywords"][0]["compaction_uid"] == receipt["uid"]
    assert metadata["entities"][0]["evidence_refs"] == [f"comment:{comment.get_uid()}"]
    from langboard.card_workspace.application.projections import public_card_timeline_entry
    event = service.get_view(actor, project, card)["records"][0]
    projected = public_card_timeline_entry(event)
    assert projected["summary"]["search_metadata"]["entities"][0]["aliases"] == ["Elixir Phoenix"]
    project, context = DomainService().card.resolve_visibility_context(project, actor, command.CollaborationChannel.Api)
    matches = service.search_metadata_candidates(actor, project, context, "Elixir")
    assert set(matches) == {card.id}
    assert set(service.search_metadata_candidates(actor, project, context, "동시편집")) == {card.id}
    assert service.search_metadata_candidates(actor, project, context, "evidence_refs") == {}
    # Native card search exposes the validated retrieval hints, never model configuration.
    results = DomainService().card.search_context_by_project(project, "Elixir", user=actor, include_closed=True)
    assert results[0]["search_metadata"]["entities"][0]["name"] == "Phoenix"
    assert "fixture-private-model-key" not in json.dumps(metadata)
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="Source no longer says that")
        db.update(comment)
    assert service.get_search_metadata(actor, project, card)["entities"] == []
    assert service.search_metadata_candidates(actor, project, context, "Elixir") == {}


@pytest.mark.parametrize("stale_count", [20, 40])
async def test_search_refills_candidates_after_stale_summary_hints(compact_command, monkeypatch, stale_count):
    service, actor, project, card, binding, *_ = compact_command

    async def provider(_, snapshot):
        return CompactionDraft(claims=draft(snapshot).claims,
            keywords=[{"value": "SearchNeedle", "evidence_refs": [snapshot.sources[0].evidence_ref]}])

    monkeypatch.setattr(command, "request_compaction_draft", provider)
    await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        original = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
        # These persisted hints refer to sources no longer belonging to their cards.
        # They must not consume the search result limit and hide the older valid card.
        for index in range(stale_count):
            newer = Card(project_id=project.id, project_column_id=card.project_column_id, title=f"Stale {index}")
            db.insert(newer)
            db.insert(CardCommentCompaction(request_key=f"stale-search-{index}", card_id=newer.id,
                user_id=actor.id, internal_bot_id=binding.id, source_revision=original.source_revision,
                provider_revision=original.provider_revision, source_manifest=original.source_manifest,
                status="completed", draft=original.draft))
    project, context = DomainService().card.resolve_visibility_context(project, actor, command.CollaborationChannel.Api)
    assert set(service.search_metadata_candidates(actor, project, context, "SearchNeedle")) == {card.id}


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
    assert result["generated_by"] == {"uid": binding.get_uid(), "display_name": binding.display_name}
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.id == comment.id)).first()
        comments = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.card_id == card.id)).all()
    after = current.model_dump(mode="json")
    for field in ("created_at", "updated_at"):
        assert datetime.fromisoformat(after.pop(field)) == datetime.fromisoformat(before.pop(field))
    assert after == before and len(comments) == 2
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
    def with_metadata(_, snapshot):
        return CompactionDraft(claims=draft(snapshot).claims,
            keywords=[{"value": "EvidenceKeyword", "evidence_refs": [snapshot.sources[0].evidence_ref]}])
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=with_metadata))
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed"
    assert service.get_search_metadata(actor, project, card)["keywords"][0]["value"] == "EvidenceKeyword"
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
    if change == "read_permission":
        with pytest.raises(CommentCompactionError):
            service.get_search_metadata(actor, project, card)
    else:
        assert service.get_search_metadata(actor, project, card)["keywords"] == []



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
        metadata_endpoint = f"/board/{project.get_uid()}/card/{card.get_uid()}/search-metadata"
        assert client.get(metadata_endpoint).status_code == 401
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
        metadata = client.get(metadata_endpoint, headers=headers)
        assert metadata.status_code == 200 and metadata.headers["Cache-Control"] == "no-store"
        assert metadata.json() == {"source": "comment_compaction", "derived": True, "keywords": [], "entities": []}
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


@pytest.mark.parametrize("history_size", [2, 12])
async def test_compact_view_sql_budget_keeps_one_cumulative_block_and_new_original(compact_command, monkeypatch, history_size):
    service, actor, project, card, *_ = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    result = await service.generate(actor, project, card)
    for index in range(history_size):
        with DbSession.atomic() as db:
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content=f"Next source {index}")))
        result = await service.generate(actor, project, card, after=result["range_end"])
    with DbSession.atomic() as db:
        uncovered = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Uncovered original"))
        db.insert(uncovered)
    engine = DbEngine.get_main_engine()
    selects = []
    def count_select(_, __, statement, ___, ____, _____):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(True)
    event.listen(engine, "before_cursor_execute", count_select)
    try:
        view = service.get_view(actor, project, card)
    finally:
        event.remove(engine, "before_cursor_execute", count_select)
    assert len([row for row in view["records"] if row["kind"] == "compaction"]) == 1
    assert [row["uid"] for row in view["records"] if row["kind"] == "comment"] == [uncovered.get_uid()]
    assert view["total_count"] == 2 and view["eligibility"]["uncompacted_event_count"] == 1
    print(f"COMPACTION_VIEW_SQL_SELECTS={len(selects)}")
    # Historical summaries remain persisted, but superseded originals need not
    # be queried again. Three and thirteen cumulative summaries both use 32
    # SELECTs, including final current-source and current-authority validation.
    assert len(selects) <= 40


@pytest.mark.parametrize("change", ["source", "read_permission", "provider"])
async def test_compact_view_rechecks_after_original_projection(compact_command, monkeypatch, change):
    from langboard_shared.domain.services.factory.ActivityService import ActivityService

    service, actor, project, card, binding, comment, _, role = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    assert (await service.generate(actor, project, card))["status"] == "completed"
    activity = service._get_service(ActivityService)
    original = activity.get_card_timeline
    def project_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        with DbSession.atomic() as db:
            if change == "source":
                comment.content = EditorContentModel(content="Changed after projection")
                db.update(comment)
            elif change == "read_permission":
                role.actions = []
                db.update(role)
            else:
                binding.is_default = False
                db.update(binding)
        return result
    monkeypatch.setattr(activity, "get_card_timeline", project_then_change)
    with pytest.raises(CommentCompactionError) as denied:
        service.get_view(actor, project, card)
    assert denied.value.status == 403 if change == "read_permission" else denied.value.code == "source_changed"


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
            assert default.data["records"][0]["summary"]["generated_by"] == {"uid": binding.get_uid(), "display_name": binding.display_name}
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
            assert seen[-1]["summary"]["generated_by"] == {"uid": binding.get_uid(), "display_name": binding.display_name}
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
            current_bundle = await client.call_tool("get_card_bundle", {**arguments, "include": ["comments"], "comments_limit": 20})
            current_items = current_bundle.structured_content["card"]["comments"]["items"]
            assert {item["kind"] for item in current_items} == {"comment", "activity"}
            restored = next(item for item in current_items if item["uid"] == comment.get_uid())
            assert json.loads(restored["content"])["content"] == "Changed original"
            movement = next(item for item in current_items if item["kind"] == "activity")
            assert movement["from_column"] == {"name": "Request"} and movement["to_column"] == {"name": "Doing"}
            with DbSession.atomic() as db:
                card.owner_user_id, card.created_by_user_id = 2, 2
                db.update(card)
            denied = await client.call_tool("get_card_timeline", source_args, raise_on_error=False)
            assert denied.is_error and "Changed original" not in str(denied.content)
            assert model.await_count == 1
    finally:
        mcp_auth_context.reset(token)


def test_mcp_compactor_attribution_never_forwards_profile_configuration():
    from langboard.card_workspace.application.projections import public_card_timeline_entry

    event = {"kind": "compaction", "uid": "summary", "data": {
        "draft": {"claims": []},
        "request_key": "private-request-key",
        "generated_by": {"uid": "compactor", "display_name": "컴팩터", "value": {"api_key": "secret"},
                         "email": "private@example.invalid", "tools": ["private-tool"]},
    }}
    result = public_card_timeline_entry(event)
    assert result["summary"]["generated_by"] == {"uid": "compactor", "display_name": "컴팩터"}
    assert not any(value in str(result) for value in ("secret", "private@example.invalid", "private-tool", "private-request-key"))
    del event["data"]["generated_by"]
    assert "generated_by" not in public_card_timeline_entry(event)["summary"]


async def test_automatic_threshold_counts_only_uncompressed_comment_bodies(automatic_command, monkeypatch):
    (service, actor, project, card, binding, comment, *_), _, stage = automatic_command
    provider = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", provider)
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.card_id == card.id, CardComment.id != comment.id)
                .values(deleted_at=SafeDateTime.now()))
        comment.content = EditorContentModel(content="한" * 999)
        db.update(comment)
    eligibility = service.get_view(actor, project, card)["eligibility"]
    assert eligibility["uncompacted_characters"] == 999
    assert eligibility["compact_after_characters"] == 1000
    assert eligibility["needs_compaction"] is False
    assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}
    provider.assert_not_awaited()
    with DbSession.atomic() as db:
        comment.content = EditorContentModel(content="한" * 1000)
        db.update(comment)
    assert service.get_view(actor, project, card)["eligibility"]["needs_compaction"] is True
    receipt = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    assert receipt["status"] == "completed"
    eligibility = service.get_view(actor, project, card)["eligibility"]
    assert eligibility["uncompacted_characters"] == 0 and eligibility["needs_compaction"] is False
    assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}
    assert provider.await_count == 1
    with DbSession.atomic() as db:
        db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="새" * 1100)))
        configure_rule(db, stage, characters=2000)
        db.update(stage)
    assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}
    assert service.get_view(actor, project, card)["eligibility"]["compact_after_characters"] == 2000


async def test_cumulative_summary_exceeds_one_original_batch_without_resending_old_text(compact_command, monkeypatch):
    service, actor, project, card, _, original, *_ = compact_command
    inputs = []
    async def summarize(_, snapshot):
        inputs.append(snapshot)
        refs = [source.evidence_ref for source in snapshot.sources[:1]]
        if snapshot.prior_summaries:
            refs.append(f"comment:{original.get_uid()}")
        return CompactionDraft(claims=[{"text": "Original unresolved work plus later reports; verification remains open",
                                       "kind": "unresolved", "evidence_refs": refs}])
    monkeypatch.setattr(command, "request_compaction_draft", summarize)
    first = await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        for index in range(101):
            db.insert(CardComment(card_id=card.id, user_id=actor.id,
                                  content=EditorContentModel(content=f"New original {index}")))
    second = await service.generate(actor, project, card)
    assert second["status"] == "completed" and len(second["evidence_refs"]) == 102
    assert len(inputs[1].sources) == 100 and len(inputs[1].prior_summaries) == 1
    assert "Original private comment" not in inputs[1].model_dump_json()
    view = service.get_view(actor, project, card)
    assert [row["uid"] for row in view["records"] if row["kind"] == "compaction"] == [second["uid"]]
    assert len([row for row in view["records"] if row["kind"] == "comment"]) == 1
    third = await service.generate(actor, project, card)
    assert third["status"] == "completed" and len(third["evidence_refs"]) == 103
    assert len(inputs[2].sources) == 1
    assert "New original 0" not in inputs[2].model_dump_json()
    assert service.get(actor, project, card, first["uid"])["status"] == "completed"
    evidence = service.get_evidence(actor, project, card, third["uid"], evidence_ref=f"comment:{original.get_uid()}")
    assert len(evidence["records"]) == 1
    assert "Original private comment" in json.dumps(evidence, default=str)
    with DbSession.atomic() as db:
        original.content = EditorContentModel(content="Edited old evidence")
        db.update(original)
    assert service.get(actor, project, card, third["uid"])["status"] == "invalidated"
    recovered = service.get_view(actor, project, card)
    assert not any(row["kind"] == "compaction" for row in recovered["records"])
    assert recovered["total_count"] == 103


async def test_large_comment_backlog_compacts_bounded_prefix_then_remaining_sources(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    with DbSession.atomic() as db:
        for index in range(6):
            db.insert(CardComment(card_id=card.id, user_id=actor.id,
                                  content=EditorContentModel(content=f"Long evidence {index}: " + "x" * 96_000)))
    inputs = []

    async def summarize(_, snapshot):
        inputs.append(snapshot)
        assert sum(len(source.text) for source in snapshot.sources) + sum(
            len(previous.draft.model_dump_json()) for previous in snapshot.prior_summaries
        ) <= 500_000
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", summarize)
    first = await service.generate(actor, project, card)
    assert first["status"] == "completed" and first["has_more_sources"]
    second = await service.generate(actor, project, card)
    assert second["status"] == "completed" and not second["has_more_sources"]
    assert len(inputs) == 2 and len(inputs[1].prior_summaries) == 1
    first_refs = {source.evidence_ref for source in inputs[0].sources}
    second_refs = {source.evidence_ref for source in inputs[1].sources}
    assert not first_refs.intersection(second_refs)
    assert len(first_refs | second_refs) == 8  # Six long comments, original comment and movement.
    assert set(second["evidence_refs"]) == first_refs | second_refs
    view = service.get_view(actor, project, card)
    assert [row["kind"] for row in view["records"]] == ["compaction"]


async def test_large_new_comment_batch_reserves_space_for_prior_summary(compact_command, monkeypatch):
    service, actor, project, card, _, original, *_ = compact_command
    prior = CompactionDraft(claims=[{"text": "x" * 1000, "kind": "unresolved",
                                    "evidence_refs": [f"comment:{original.get_uid()}"]} for _ in range(12)])
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(return_value=prior))
    first = await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        for _ in range(5):
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="x" * 99_000)))
    inputs = []

    async def summarize(_, snapshot):
        inputs.append(snapshot)
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", summarize)
    second = await service.generate(actor, project, card)
    assert second["status"] == "completed" and second["has_more_sources"]
    assert len(inputs[0].sources) == 4 and inputs[0].prior_summaries[0].draft == prior
    third = await service.generate(actor, project, card)
    assert third["status"] == "completed" and not third["has_more_sources"]
    assert len(inputs[1].sources) == 1
    assert len(third["evidence_refs"]) == len(first["evidence_refs"]) + 5


async def test_invalid_latest_summary_keeps_prior_valid_block_and_restores_new_original(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    first = await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        later = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="New evidence"))
        db.insert(later)
    latest = await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        later.content = EditorContentModel(content="Corrected new evidence")
        db.update(later)
    view = service.get_view(actor, project, card)
    assert [row["uid"] for row in view["records"] if row["kind"] == "compaction"] == [first["uid"]]
    assert [row["uid"] for row in view["records"] if row["kind"] == "comment"] == [later.get_uid()]
    assert service.get(actor, project, card, latest["uid"])["status"] == "invalidated"


async def test_prior_original_changed_during_cumulative_generation_never_publishes(compact_command, monkeypatch):
    service, actor, project, card, _, original, *_ = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="New evidence")))
    async def modify_previous(_, snapshot):
        assert snapshot.prior_summaries and len(snapshot.sources) == 1
        with DbSession.atomic() as db:
            original.content = EditorContentModel(content="Changed while a newer summary runs")
            db.update(original)
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", modify_previous)
    result = await service.generate(actor, project, card)
    assert result["status"] == "failed" and result["error_code"] == "source_changed"
    assert "draft" not in result


async def test_new_comments_growing_past_cumulative_budget_during_generation_fail_closed(compact_command, monkeypatch):
    service, actor, project, card, _, original, *_ = compact_command
    prior = CompactionDraft(claims=[{"text": "x" * 1000, "kind": "unresolved",
                                    "evidence_refs": [f"comment:{original.get_uid()}"]} for _ in range(12)])
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(return_value=prior))
    await service.generate(actor, project, card)
    with DbSession.atomic() as db:
        comments = [CardComment(card_id=card.id, user_id=actor.id,
                               content=EditorContentModel(content="x" * 96000)) for _ in range(5)]
        for comment in comments:
            db.insert(comment)
    async def grow_comments(_, snapshot):
        assert snapshot.prior_summaries and len(snapshot.sources) == 5
        with DbSession.atomic() as db:
            for comment in comments:
                comment.content = EditorContentModel(content="x" * 99000)
                db.update(comment)
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", grow_comments)
    result = await service.generate(actor, project, card)
    assert result["status"] == "failed" and result["error_code"] == "source_changed"
    assert "draft" not in result


async def test_automatic_second_summary_accumulates_prior_sources_only_after_new_threshold(automatic_command, monkeypatch):
    (service, actor, project, card, binding, *_), _, _ = automatic_command
    inputs = []
    async def summarize(_, snapshot):
        inputs.append(snapshot)
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", summarize)
    first = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    with DbSession.atomic() as db:
        db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="New evidence " * 100)))
    second = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    assert first["status"] == second["status"] == "completed"
    assert inputs[1].prior_summaries and len(inputs[1].sources) == 1
    view = service.get_view(actor, project, card)
    assert [row["uid"] for row in view["records"] if row["kind"] == "compaction"] == [second["uid"]]
    assert len(view["records"][0]["data"]["evidence_refs"]) == 21
    assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}


@pytest.fixture
def comment_actor_bot(automatic_command):
    from langboard_shared.domain.models import Bot
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType

    with DbSession.atomic() as db:
        bot = Bot(name="Comment author", bot_uname="comment-author", app_api_token="fixture",
                  platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default)
        db.insert(bot)
    return bot


@pytest.mark.parametrize("metric,authors,threshold,expected_turns", [
    ("comments", ["user", "user", "user"], 3, 1),
    ("turns", ["user", "user", "user"], 2, 1),
    ("turns", ["user", "bot", "user"], 3, 3),
    ("turns", ["user", "user", "bot"], 2, 2),
])
async def test_count_turn_boundaries_match_manual_and_automatic(
    automatic_command, comment_actor_bot, monkeypatch, metric, authors, threshold, expected_turns,
):
    (service, actor, project, card, binding, original, move, *_), _, stage = automatic_command
    provider = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", provider)
    started = datetime(2025, 1, 1)
    with DbSession.atomic() as db:
        db.exec(delete(CardComment).where(CardComment.card_id == card.id))
        configure_rule(db, stage, characters=500000, **{metric: threshold})
        db.update(stage)
        # Movement between identical actors must not split their comment run.
        move.created_at = started + timedelta(seconds=1)
        db.update(move)
        for index, author in enumerate(authors[:-1]):
            db.insert(CardComment(card_id=card.id, user_id=actor.id if author == "user" else None,
                                  bot_id=comment_actor_bot.id if author == "bot" else None,
                                  created_at=started + timedelta(seconds=index * 2),
                                  content=EditorContentModel(content="short")))
    before = service.get_view(actor, project, card)["eligibility"]
    assert before["needs_compaction"] is False
    assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}
    provider.assert_not_awaited()
    with DbSession.atomic() as db:
        author = authors[-1]
        db.insert(CardComment(card_id=card.id, user_id=actor.id if author == "user" else None,
                              bot_id=comment_actor_bot.id if author == "bot" else None,
                              created_at=started + timedelta(seconds=len(authors) * 2),
                              content=EditorContentModel(content="short")))
        db.insert(CardComment(card_id=card.id, user_id=actor.id, deleted_at=SafeDateTime.now(),
                              content=EditorContentModel(content="deleted")))
    eligibility = service.get_view(actor, project, card)["eligibility"]
    assert eligibility["uncompacted_comment_count"] == len(authors)
    assert eligibility["uncompacted_turn_count"] == expected_turns
    reached = len(authors) >= threshold if metric == "comments" else expected_turns >= threshold
    assert eligibility["needs_compaction"] is reached
    receipt = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    assert receipt["status"] == ("completed" if reached else "skipped")
    assert provider.await_count == int(reached)
    if reached:
        covered = service.get_view(actor, project, card)["eligibility"]
        assert covered["uncompacted_comment_count"] == covered["uncompacted_turn_count"] == 0
        assert covered["needs_compaction"] is False
        with DbSession.atomic() as db:
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="new")))
        fresh = service.get_view(actor, project, card)["eligibility"]
        assert fresh["uncompacted_comment_count"] == fresh["uncompacted_turn_count"] == 1
        assert fresh["needs_compaction"] is False
        assert await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2])) == {"status": "skipped", "reason": "below_threshold"}
        assert provider.await_count == 1


@pytest.mark.parametrize("policy,characters,comments,turns,expected", [
    ({}, 11999, 100, 100, False),
    ({}, 12000, 0, 0, True),
    ({"compact_after_comments": 3, "compact_after_turns": 2}, 1, 3, 1, True),
    ({"compact_after_comments": 3, "compact_after_turns": 2}, 1, 2, 2, True),
    ({"compact_after_comments": 3, "compact_after_turns": 2}, 12000, 1, 1, True),
    ({"compact_after_comments": 3, "compact_after_turns": 2}, 11999, 2, 1, False),
    ({"compact_after_comments": 100000, "compact_after_turns": 100000}, 1, 99999, 99999, False),
    ({"compact_after_comments": 100000, "compact_after_turns": 100000}, 1, 100000, 99999, True),
])
def test_shared_compaction_predicate_or_and_legacy_boundaries(policy, characters, comments, turns, expected):
    from langboard_shared.ai.comment_compaction import compaction_threshold_reached, compaction_thresholds

    assert compaction_threshold_reached(compaction_thresholds(policy), characters=characters,
                                        comments=comments, turns=turns) is expected


def test_comment_volume_aggregates_beyond_snapshot_limit_in_one_query(automatic_command, comment_actor_bot):
    (service, actor, project, card, binding, *_), _, _ = automatic_command
    same_time = datetime(2025, 1, 1)
    with DbSession.atomic() as db:
        db.exec(delete(CardComment).where(CardComment.card_id == card.id))
        for index in range(102):
            db.insert(CardComment(card_id=card.id, user_id=actor.id if index < 101 else None,
                                  bot_id=comment_actor_bot.id if index == 101 else None, created_at=same_time,
                                  content=EditorContentModel(content="한")))
    statements = []
    engine = DbEngine.get_main_engine()
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with DbSession.use(readonly=False) as db:
            volume = service._uncovered_comment_volume(db, project, card, set())
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert volume == {"comments": 102, "characters": 102, "turns": 2}
    assert len(statements) == 1
    assert "lag(" in statements[0].lower() and "count(" in statements[0].lower()


def disjoint_prior_blocks(compact_command, *, large=False):
    service, actor, project, card, binding, *_ = compact_command
    records = []
    repository = service.repo.card_comment_compaction
    for index in range(21):
        with DbSession.atomic() as db:
            comment = CardComment(card_id=card.id, user_id=actor.id,
                                  content=EditorContentModel(content=f"Covered original {index}"))
            db.insert(comment)
        snapshot, _ = service._snapshot(project, card, refs=[f"comment:{comment.get_uid()}"])
        prior = draft(snapshot)
        data = prior.model_dump()
        data["keywords"] = [{"value": f"retained-keyword-{index}", "evidence_refs": [snapshot.sources[0].evidence_ref]}]
        prior = CompactionDraft.model_validate(data)
        if large:
            prior.claims = [prior.claims[0].model_copy(update={"text": "x" * 1000}) for _ in range(12)]
            data = prior.model_dump()
            data["entities"] = [{"name": "n" * 120, "kind": "other", "aliases": ["a" * 80] * 5,
                               "evidence_refs": [snapshot.sources[0].evidence_ref]} for _ in range(20)]
            prior = CompactionDraft.model_validate(data)
        record = repository.claim(repository.reserve(card, actor, binding, snapshot))
        assert repository.complete(record, record.attempt, snapshot, binding, prior)
        records.append(record)
    return records


async def test_cumulative_selects_bounded_prior_blocks_preserving_unselected_evidence(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    records = disjoint_prior_blocks(compact_command)
    originals = {record.id: record.source_manifest for record in records}
    inputs = []
    async def summarize(_, snapshot):
        inputs.append(snapshot)
        assert len(snapshot.prior_summaries) == 20
        assert "Covered original" not in snapshot.model_dump_json()
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", summarize)
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed" and not result["has_more_sources"]
    assert not service.get(actor, project, card, result["uid"])["has_more_sources"]
    assert not (await service.generate(actor, project, card, expected_request_key=result["request_key"]))["has_more_sources"]
    assert not next(row for row in service.get_page(actor, project, card)["records"]
                    if row["uid"] == result["uid"])["has_more_sources"]
    assert len(inputs) == 1
    assert len(result["evidence_refs"]) == 22  # 20 priors, original comment, movement.
    blocks = service._valid_blocks(actor, project, card, channel=command.CollaborationChannel.Api)
    assert len(blocks) == 2 and set().union(*(set(block["evidence_refs"]) for block in blocks)) == {
        *result["evidence_refs"], records[-1].source_manifest[0]["evidence_ref"]}
    assert any(item["value"] == "retained-keyword-20" for item in service.get_search_metadata(actor, project, card)["keywords"])
    for record in records:
        assert service.get(actor, project, card, record.id)["status"] == "completed"
        with DbSession.use(readonly=False) as db:
            saved = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(CardCommentCompaction.id == record.id)).first()
        assert saved.source_manifest == originals[record.id]


async def test_cumulative_prior_character_budget_and_required_refs_retry(compact_command, monkeypatch):
    service, actor, project, card, *_ = compact_command
    disjoint_prior_blocks(compact_command, large=True)
    with DbSession.atomic() as db:
        for _ in range(6):
            db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="z" * 96000)))
    inputs = []
    async def summarize(_, snapshot):
        inputs.append(snapshot)
        assert 0 < len(snapshot.prior_summaries) < 20
        assert sum(len(source.text) for source in snapshot.sources) + sum(
            len(previous.draft.model_dump_json()) for previous in snapshot.prior_summaries) <= 500000
        if len(inputs) == 1:
            raise RuntimeError("Transient provider failure")
        return draft(snapshot)
    monkeypatch.setattr(command, "request_compaction_draft", summarize)
    failed = await service.generate(actor, project, card)
    assert failed["status"] == "failed" and failed["error_code"] == "provider_error"
    # A retry must retain the reserved manifest despite newly arrived evidence.
    with DbSession.atomic() as db:
        unrelated = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Arrived after failure"))
        db.insert(unrelated)
    retried = await service.generate(actor, project, card, retry=True, expected_request_key=failed["request_key"])
    assert retried["status"] == "completed" and retried["has_more_sources"]
    assert inputs[0].evidence_manifest() == inputs[1].evidence_manifest()
    assert inputs[0].prior_summaries == inputs[1].prior_summaries
    assert f"comment:{unrelated.get_uid()}" not in retried["evidence_refs"]


@pytest.mark.parametrize("kind", ["comment", "activity"])
async def test_completed_readback_finds_uncovered_older_events(compact_command, monkeypatch, kind):
    service, actor, project, card, _, comment, *_ = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    result = await service.generate(actor, project, card)
    older = comment.created_at - timedelta(days=1)
    with DbSession.atomic() as db:
        if kind == "comment":
            db.insert(CardComment(card_id=card.id, user_id=actor.id, created_at=older,
                                  content=EditorContentModel(content="Earlier original not yet compressed")))
        else:
            db.insert(ProjectActivity(project_id=project.id, card_id=card.id, user_id=actor.id, created_at=older,
                activity_type=ProjectActivityType.CardMoved,
                activity_history={"from_column": {"name": "Backlog"}, "column": {"name": "Request"}}))
    assert service.get(actor, project, card, result["uid"])["has_more_sources"]
    assert (await service.generate(actor, project, card, expected_request_key=result["request_key"]))["has_more_sources"]
    assert service.get_page(actor, project, card)["records"][0]["has_more_sources"]


async def test_readback_coverage_excludes_invalid_blocks_and_superseded_overlap(compact_command, monkeypatch):
    service, actor, project, card, binding, comment, move, _ = compact_command
    repository = service.repo.card_comment_compaction
    refs = [f"comment:{comment.get_uid()}", f"activity:{move.get_uid()}"]
    # A valid old block covers both events. A newer overlapping block covers
    # only the movement, leaving the comment visible under latest-wins rules.
    whole, _ = service._snapshot(project, card, refs=refs)
    old = repository.claim(repository.reserve(card, actor, binding, whole))
    assert repository.complete(old, old.attempt, whole, binding, draft(whole))
    movement, _ = service._snapshot(project, card, refs=[refs[1]])
    new = repository.claim(repository.reserve(card, actor, binding, movement))
    assert repository.complete(new, new.attempt, movement, binding, draft(movement))
    assert service.get(actor, project, card, new.id)["has_more_sources"]
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    latest = await service.generate(actor, project, card)
    assert not latest["has_more_sources"]
    # Invalidating the newest whole block must restore its changed source;
    # stale manifests and superseded older blocks cannot cover it.
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(
            content=EditorContentModel(content="Changed original after compression")))
    assert service.get(actor, project, card, latest["uid"])["status"] == "invalidated"
    assert service.get(actor, project, card, new.id)["has_more_sources"]


@pytest.mark.parametrize("size", [120_001, 510_001])
async def test_oversized_original_is_fully_consumed_before_publication(compact_command, monkeypatch, size):
    service, actor, project, card, binding, comment, *_ = compact_command
    from src.graph.langboard_graph.core.schema.GraphRequestModel import validate_graph_payload

    body = "Beginning " + "한" * size + " Ending"
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(content=EditorContentModel(content=body)))
        following = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Following new evidence"))
        db.insert(following)
    original, _ = service._snapshot(project, card, refs=[f"comment:{comment.get_uid()}"])
    ref = original.sources[0].evidence_ref
    portions = []

    async def generate(_, snapshot):
        # This is exactly the wire/provider model, with existing caps intact.
        CommentCompactionSnapshot.model_validate_json(snapshot.model_dump_json())
        validate_graph_payload({**snapshot.model_dump(mode="json"), "model_settings": json.loads(binding.value),
                                "snapshot_revision": snapshot.source_revision()})
        assert all(len(source.text) <= 100_000 for source in snapshot.sources)
        with DbSession.use(readonly=False) as db:
            rows = db.exec(SqlBuilder.select.table(CardCommentCompaction)).all()
            assert all(row.draft is None for row in rows if row.status == "running")
        portions.extend(source.text for source in snapshot.sources if source.evidence_ref == ref)
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    completed = await service.generate(actor, project, card)
    assert completed["status"] == "completed"
    assert "".join(portions) == original.sources[0].text and len(portions) > 1
    with DbSession.use(readonly=False) as db:
        row = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
            CardCommentCompaction.id == command.InfraHelper.convert_id(completed["uid"]))).first()
        assert [entry for entry in row.source_manifest if entry["evidence_ref"] == ref] == original.evidence_manifest()
        saved = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.id == comment.id)).first()
        assert saved.content.content == body
    if completed["has_more_sources"]:
        completed = await service.generate(actor, project, card)
    assert f"comment:{following.get_uid()}" in completed["evidence_refs"]
    assert completed["generated_by"]["uid"] == binding.get_uid()
    assert not completed["has_more_sources"]


async def test_oversized_retry_replays_exact_full_original_without_partial_draft(compact_command, monkeypatch):
    service, actor, project, card, _, comment, *_ = compact_command
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(content=EditorContentModel(content="x" * 510_001)))
    calls = []

    async def generate(_, snapshot):
        calls.append(snapshot.model_dump_json())
        if len(calls) == 2:
            raise RuntimeError("fixture provider failure")
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    failed = await service.generate(actor, project, card)
    assert failed["status"] == "failed" and "draft" not in failed
    with DbSession.atomic() as db:
        db.insert(CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="Arrived during retry")))
    retried = await service.generate(actor, project, card, retry=True, expected_request_key=failed["request_key"])
    assert retried["status"] == "completed" and retried["has_more_sources"]
    assert calls[:2] == calls[2:4]


@pytest.mark.parametrize("change", ["edit", "revoke", "provider", "cancel", "expire"])
async def test_oversized_generation_stops_before_next_portion_when_invalidated(compact_command, monkeypatch, change):
    service, actor, project, card, binding, comment, _, role = compact_command
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(content=EditorContentModel(content="x" * 510_001)))
    calls = 0

    async def generate(_, snapshot):
        nonlocal calls
        calls += 1
        with DbSession.atomic() as db:
            if change == "edit":
                db.exec(update(CardComment).where(CardComment.id == comment.id).values(content=EditorContentModel(content="Edited")))
            elif change == "revoke":
                db.exec(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=["read"]))
            elif change == "provider":
                db.exec(update(InternalBot).where(InternalBot.id == binding.id).values(updated_at=SafeDateTime.now() + timedelta(seconds=1)))
            elif change == "cancel":
                row = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
                service.repo.card_comment_compaction.cancel(row)
            else:
                db.exec(update(CardCommentCompaction).values(lease_expires_at=SafeDateTime.now() - timedelta(seconds=1)))
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    result = await service.generate(actor, project, card)
    assert calls == 1 and result["status"] in {"failed", "cancelled", "retry_required"} and "draft" not in result
    with DbSession.use(readonly=False) as db:
        assert all(row.draft is None for row in db.exec(SqlBuilder.select.table(CardCommentCompaction)).all())


@pytest.mark.parametrize("change", ["edit", "revoke"])
async def test_automatic_oversized_generation_stops_after_invalidated_portion(automatic_command, monkeypatch, change):
    from langboard_shared.domain.services.CompactorBoardAuthority import set_compactor_board_grant

    (service, actor, project, card, binding, comment, *_), _, _ = automatic_command
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(content=EditorContentModel(content="x" * 510_001)))
    calls = 0

    async def generate(_, snapshot):
        nonlocal calls
        calls += 1
        if change == "edit":
            with DbSession.atomic() as db:
                db.exec(update(CardComment).where(CardComment.id == comment.id).values(content=EditorContentModel(content="Edited")))
        else:
            set_compactor_board_grant(actor, project.id, binding.id, can_read=True, can_compact=False, can_archive=False)
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    result = await service.generate_automatic(project.id, binding.id, card.id, rule_id=rule_id(automatic_command[2]))
    assert calls == 1 and result["status"] == "failed"
    assert result["error_code"] in {"source_changed", "permission_changed"}


@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_invalidated_completed_replay_reports_current_uncovered_sources(compact_command, monkeypatch, change):
    service, actor, project, card, _, comment, move, _ = compact_command
    provider = AsyncMock(side_effect=lambda _, snapshot: draft(snapshot))
    monkeypatch.setattr(command, "request_compaction_draft", provider)
    completed = await service.generate(actor, project, card)
    assert completed["status"] == "completed" and not completed["has_more_sources"]
    with DbSession.atomic() as db:
        values = ({"content": EditorContentModel(content="Corrected original evidence")}
                  if change == "edit" else {"deleted_at": SafeDateTime.now()})
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(**values))
    replay = await service.generate(actor, project, card, expected_request_key=completed["request_key"])
    assert replay["status"] == "invalidated" and replay["has_more_sources"]
    assert "draft" not in replay and provider.await_count == 1
    assert service.get(actor, project, card, completed["uid"])["has_more_sources"]
    assert service.get_page(actor, project, card)["records"][0]["has_more_sources"]
    view = service.get_view(actor, project, card)
    assert not any(row["kind"] == "compaction" for row in view["records"])
    assert any(row["evidence_ref"] == f"activity:{move.get_uid()}" for row in view["records"])
    repaired = await service.generate(actor, project, card)
    assert repaired["status"] == "completed" and not repaired["has_more_sources"]
    assert (f"comment:{comment.get_uid()}" in repaired["evidence_refs"]) is (change == "edit")
