"""Exercise migration, non-destructive persistence, request coalescing and fencing."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.ai.comment_compaction import CompactionDraft
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import CardComment, CardCommentCompaction, CardMetadata, InternalBot, User
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError


def test_reservation_coalesces_only_the_same_actor_source_and_provider(compactions):
    repo, actor, card, binding, comment, snapshot, *_ = compactions
    # Compare two persisted projections: PostgreSQL may return the same instant
    # in the session timezone rather than the constructor's UTC representation.
    with DbSession.use(readonly=False) as db:
        original = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.id == comment.id)).first().model_dump(mode="json")
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
    assert retried is not None and retried.attempt == record.attempt + 1


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


def test_metadata_and_summary_commit_atomically_without_duplicate_completion(compactions, monkeypatch):
    repo, actor, card, binding, _, snapshot, draft, *_ = compactions
    record = repo.claim(repo.reserve(card, actor, binding, snapshot))
    original_insert = DbSession.insert
    def reject_metadata(self, obj):
        if isinstance(obj, CardMetadata):
            raise RuntimeError("fixture storage failure")
        return original_insert(self, obj)
    with monkeypatch.context() as patch:
        patch.setattr(DbSession, "insert", reject_metadata)
        with pytest.raises(RuntimeError, match="storage failure"):
            repo.complete(record, record.attempt, snapshot, binding, draft)
    with DbSession.use(readonly=False) as db:
        saved = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(CardCommentCompaction.id == record.id)).first()
        assert saved.status == "running" and saved.draft is None
        assert db.exec(SqlBuilder.select.table(CardMetadata)).all() == []
    assert repo.complete(record, record.attempt, snapshot, binding, draft)
    assert not repo.complete(record, record.attempt, snapshot, binding, draft)
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(CardMetadata)).all()) == 1


def test_sequential_lease_renewal_preserves_attempt_and_fences_expiry_cancel_retry(compactions):
    repo, actor, card, binding, _, snapshot, *_ = compactions
    now = SafeDateTime.now()
    record = repo.reserve(card, actor, binding, snapshot)
    first = repo.claim(record, now=now, lease_seconds=2)
    assert repo.renew(record, first.attempt, now=now + timedelta(seconds=1), lease_seconds=5)
    assert repo.renew(record, first.attempt, now=now + timedelta(seconds=4), lease_seconds=5)
    assert not repo.renew(record, first.attempt, now=now + timedelta(seconds=10))
    second = repo.claim(record, retry=True, now=now + timedelta(seconds=10))
    assert second.attempt == first.attempt + 1
    assert not repo.renew(record, first.attempt, now=now + timedelta(seconds=11))
    assert repo.cancel(record)
    assert not repo.renew(record, second.attempt, now=now + timedelta(seconds=11))
