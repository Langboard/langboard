"""Exercise migration, non-destructive persistence, request coalescing and fencing."""

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.ai.comment_compaction import CommentCompactionSnapshot, CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, CardComment, CardCommentCompaction, InternalBot, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.infrastructure.repositories.factory.CardCommentCompactionRepository import (
    CardCommentCompactionRepository,
)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def compactions(board):
    _, actor, project, _, _, columns, *_ = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardComment, InternalBot):
        model.__table__.create(engine)
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261021020000-c4a8f16d3092.py"
    spec = importlib.util.spec_from_file_location("compaction_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    with DbSession.atomic() as db:
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Original card")
        binding = InternalBot(bot_type=InternalBotType.CommentCompaction, display_name="Fixture",
                              platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
                              value='{"model_name":"fixture"}', api_key="private-fixture-credential")
        db.insert(card)
        db.insert(binding)
        comment = CardComment(card_id=card.id, user_id=actor.id,
                              content=EditorContentModel(content="Original private comment"))
        db.insert(comment)
    snapshot = CommentCompactionSnapshot(sources=[{
        "evidence_ref": f"comment:{comment.get_uid()}", "created_at": comment.created_at,
        "updated_at": comment.updated_at, "text": comment.content.content,
    }])
    draft = CompactionDraft(claims=[{"text": "Reported work remains unverified", "kind": "unresolved",
                                    "evidence_refs": [snapshot.sources[0].evidence_ref]}])
    return CardCommentCompactionRepository(None, None), actor, card, binding, comment, snapshot, draft, migration


def test_reservation_coalesces_only_the_same_actor_source_and_provider(compactions):
    repo, actor, card, binding, comment, snapshot, *_ = compactions
    original = comment.model_dump(mode="json")
    first = repo.reserve(card, actor, binding, snapshot)
    assert repo.reserve(card, actor, binding, snapshot).id == first.id
    with DbSession.use(readonly=False) as db:
        other = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
        fresh_binding = db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.id == binding.id)).first()
    assert repo.reserve(card, actor, fresh_binding, snapshot).id == first.id
    assert repo.reserve(card, other, binding, snapshot).id != first.id
    edited = snapshot.model_copy(deep=True)
    edited.sources[0].text = "Edited source"
    assert repo.reserve(card, actor, binding, edited).id != first.id
    with DbSession.use(readonly=False) as db:
        binding.value = '{"model_name":"changed-model"}'
        db.update(binding)
    assert repo.reserve(card, actor, binding, snapshot).id != first.id
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.id == comment.id)).first()
        persisted = db.exec(SqlBuilder.select.table(CardCommentCompaction)).all()
    assert current.model_dump(mode="json") == original
    serialized = json.dumps([row.model_dump(mode="json") for row in persisted])
    assert "Original private comment" not in serialized and "private-fixture-credential" not in serialized


def test_lease_requires_explicit_retry_and_fences_late_model_responses(compactions):
    repo, actor, card, binding, _, snapshot, draft, *_ = compactions
    record = repo.reserve(card, actor, binding, snapshot)
    now = SafeDateTime.now()
    first = repo.claim(record, now=now, lease_seconds=1)
    assert first.attempt == 1 and repo.claim(record, now=now) is None
    later = now + timedelta(seconds=2)
    assert repo.claim(record, now=later) is None
    second = repo.claim(record, retry=True, now=later)
    assert second.attempt == 2
    assert not repo.complete(record, first.attempt, snapshot, binding, draft, now=later)
    assert not repo.fail(record, first.attempt, "provider_error")
    assert repo.complete(record, second.attempt, snapshot, binding, draft, now=later)
    assert repo.claim(record, retry=True, now=later) is None
    assert repo.reserve(card, actor, binding, snapshot).status == "completed"


def test_changed_sources_or_provider_and_unknown_citations_cannot_complete(compactions):
    repo, actor, card, binding, _, snapshot, draft, *_ = compactions
    record = repo.claim(repo.reserve(card, actor, binding, snapshot))
    changed = snapshot.model_copy(deep=True)
    changed.sources[0].text = "A new version"
    assert not repo.complete(record, record.attempt, changed, binding, draft)
    other_binding = binding.model_copy(deep=True)
    other_binding.updated_at += timedelta(seconds=1)
    assert not repo.complete(record, record.attempt, snapshot, other_binding, draft)
    outside = CompactionDraft(claims=[{"text": "Invalid", "kind": "fact", "evidence_refs": ["comment:outside"]}])
    with pytest.raises(ValueError, match="unknown evidence"):
        repo.complete(record, record.attempt, snapshot, binding, outside)
    assert repo.fail(record, record.attempt, "source_changed")
    assert repo.reserve(card, actor, binding, snapshot).draft is None


@pytest.mark.parametrize("code", ["provider_error", "permission_changed", "cancelled"])
def test_failure_records_only_known_codes_and_never_a_partial_summary(compactions, code):
    repo, actor, card, binding, _, snapshot, *_ = compactions
    record = repo.claim(repo.reserve(card, actor, binding, snapshot))
    with pytest.raises(ValueError, match="error code"):
        repo.fail(record, record.attempt, "upstream-secret-and-body")
    assert repo.fail(record, record.attempt, code)
    saved = repo.reserve(card, actor, binding, snapshot)
    assert saved.error_code == code and saved.draft is None
    assert repo.claim(saved) is None
    retried = repo.claim(saved, retry=True)
    assert (retried is None) == (code == "cancelled")


def test_migration_cannot_discard_existing_compaction_evidence(compactions):
    repo, actor, card, binding, _, snapshot, _, migration = compactions
    repo.reserve(card, actor, binding, snapshot)
    with DbEngine.get_main_engine().begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
    assert repo.reserve(card, actor, binding, snapshot).status == "pending"


@pytest.mark.parametrize("values", [{"status": "invented"}, {"attempt": -1}, {"status": "completed"}, {"draft": {"claims": []}}])
def test_database_rejects_invalid_run_states_and_partial_drafts(compactions, values):
    repo, actor, card, binding, _, snapshot, *_ = compactions
    record = repo.reserve(card, actor, binding, snapshot)
    with pytest.raises(IntegrityError), DbSession.use(readonly=False) as db:
        db.exec(update(CardCommentCompaction).where(CardCommentCompaction.id == record.id).values(**values))
    assert repo.reserve(card, actor, binding, snapshot).status == "pending"


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_concurrent_requests_reserve_one_run_and_claim_one_attempt(compactions):
    repo, actor, card, binding, _, snapshot, *_ = compactions
    barrier = Barrier(2)
    def reserve():
        barrier.wait(timeout=5)
        return repo.reserve(card, actor, binding, snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        records = [future.result(timeout=10) for future in futures]
    assert records[0].id == records[1].id
    def claim():
        barrier.wait(timeout=5)
        return repo.claim(records[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1
