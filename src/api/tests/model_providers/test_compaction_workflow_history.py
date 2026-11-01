"""Exercise actual attempt fencing and atomic workflow receipts."""

from datetime import timedelta
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ProjectColumn, WorkflowEffectRun, WorkflowStageDefinition


def _bind_stage(card):
    with DbSession.atomic() as db:
        stage = WorkflowStageDefinition(key="history_fixture", name="Review", color="#64748B")
        db.insert(stage)
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        column.workflow_stage = stage.key
        db.update(column)
    return stage


def _runs():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(WorkflowEffectRun).order_by(WorkflowEffectRun.id)).all()


@pytest.mark.parametrize("outcome", ["success", "provider_error", "cancel", "retry", "rollback", "unbound"])
def test_compaction_attempts_record_profile_status_and_preserve_failed_receipts(compactions, outcome):
    repo, actor, card, binding, _, snapshot, draft, _ = compactions
    stage = _bind_stage(card) if outcome != "unbound" else None
    if outcome == "unbound":
        with DbSession.atomic() as db:
            column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
            column.workflow_stage = None
            db.update(column)
    record = repo.reserve(card, actor, binding, snapshot)
    now = SafeDateTime.now()
    if outcome == "rollback":
        with pytest.raises(RuntimeError, match="rollback"):
            with DbSession.atomic():
                assert repo.claim(record, now=now)
                assert len(_runs()) == 1
                raise RuntimeError("rollback")
        assert _runs() == []
        return
    assert repo.claim(record, now=now, lease_seconds=60)
    assert repo.claim(record, now=now) is None
    runs = _runs()
    if outcome == "unbound":
        assert runs == []
        assert repo.complete(record, 1, snapshot, binding, draft, now=now)
        assert _runs() == []
        return
    run = runs[0]
    assert (run.stage_id, run.stage_revision) == (stage.id, stage.edit_revision())
    assert (run.profile_id, run.profile_name, run.actor_id, run.actor_type) == (binding.id, binding.display_name, actor.id, "user")
    assert run.status == "running" and run.source == "manual"
    assert run.effects == ["compact_comments"]
    if outcome == "success":
        # Moving or renaming the column after acceptance cannot rewrite the original stage receipt.
        with DbSession.atomic() as db:
            column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
            column.workflow_stage = None
            db.update(column)
        assert repo.complete(record, 1, snapshot, binding, draft, now=now + timedelta(seconds=1))
        assert _runs()[0].stage_id == stage.id
    elif outcome == "provider_error":
        assert repo.fail(record, 1, "provider_error")
    elif outcome == "cancel":
        assert repo.cancel(record)
        assert not repo.complete(record, 1, snapshot, binding, draft, now=now)
    elif outcome == "retry":
        later = now + timedelta(seconds=61)
        assert repo.claim(record, retry=True, now=later)
        assert not repo.complete(record, 1, snapshot, binding, draft, now=later)
        runs = _runs()
        assert len(runs) == 2 and runs[0].status == "uncertain" and runs[0].error_code == "lease_expired"
        assert repo.complete(record, 2, snapshot, binding, draft, now=later + timedelta(seconds=1))
    latest = _runs()[-1]
    expected = {"success": "succeeded", "provider_error": "failed", "cancel": "cancelled", "retry": "succeeded"}[outcome]
    assert latest.status == expected
    assert latest.result == {"compacted": int(expected == "succeeded")}
    assert latest.duration_ms >= 0
    assert latest.error_code == ({"failed": "provider_error", "cancelled": "cancelled"}.get(expected))


@pytest.mark.parametrize("outcome", ["success", "provider_error", "cancel", "retry"])
def test_automatic_attempts_are_profile_owned_without_a_borrowed_user(compactions, outcome):
    repo, actor, card, binding, comment, snapshot, draft, _ = compactions
    stage = _bind_stage(card)
    original = comment.model_dump(mode="json")
    manual = repo.reserve(card, actor, binding, snapshot)
    automatic = repo.reserve_automatic(card, binding, snapshot)
    assert automatic.user_id is None and automatic.internal_bot_id == binding.id
    assert automatic.request_key != manual.request_key
    assert repo.reserve_automatic(card, binding, snapshot).id == automatic.id
    now = SafeDateTime.now()
    claimed = repo.claim(automatic, now=now, lease_seconds=60)
    assert claimed.attempt == 1
    first = _runs()[0]
    assert (first.actor_id, first.actor_type, first.source) == (binding.id, "profile", "continuous")
    assert (first.profile_id, first.profile_name, first.stage_id) == (binding.id, binding.display_name, stage.id)
    if outcome == "success":
        assert repo.complete(automatic, 1, snapshot, binding, draft, now=now)
    elif outcome == "provider_error":
        assert repo.fail(automatic, 1, "provider_error")
    elif outcome == "cancel":
        assert repo.cancel(automatic)
        assert not repo.complete(automatic, 1, snapshot, binding, draft, now=now)
    else:
        later = now + timedelta(seconds=61)
        assert repo.claim(automatic, retry=True, now=later)
        assert not repo.complete(automatic, 1, snapshot, binding, draft, now=later)
        assert repo.complete(automatic, 2, snapshot, binding, draft, now=later)
        assert _runs()[0].status == "uncertain"
    assert all(run.actor_type == "profile" and run.actor_id == binding.id for run in _runs())
    assert manual.user_id == actor.id and manual.status == "pending"
    assert comment.model_dump(mode="json") == original
