"""Native archive movement retains explicit profile authority and atomic receipts."""

import importlib
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAppOwnership,
    CardRelationship,
    CardVerificationRecord,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    ProjectColumn,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CompactorBoardAuthority import CompactorAuthorityError
from langboard_shared.domain.services.factory.CardService import CardService
from sqlalchemy import select


module = importlib.import_module(CardService.__module__)


@pytest.fixture
def archive_command(compactions, board, monkeypatch):
    _, actor, card, profile, *_ = compactions
    project = board[2]
    for model in (CardAppOwnership, GlobalCardRelationshipType, CardRelationship, CardVerificationRecord,
                  Checklist, Checkitem, CompactorBoardGrant):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        column.workflow_stage = "archive_ready"
        db.update(column)
        stage = WorkflowStageDefinition(key="archive_ready", name="Verified work", counts_as_completed=True,
                                        continuous_effects={"auto_archive": True, "archive_after_seconds": 3600})
        db.insert(stage)
        grant = CompactorBoardGrant(project_id=project.id, internal_bot_id=profile.id,
                                   granted_by_user_id=actor.id, can_read=True, can_compact=False, can_archive=True)
        db.insert(grant)
        card.workflow_stage_entered_key = stage.key
        card.workflow_stage_entered_at = now - timedelta(hours=2)
        card.last_change_seq = 7
        db.update(card)
        verification = CardVerificationRecord(card_id=card.id, source_change_seq=7, decision="verified",
            recorded_by_user_id=actor.id, evidence=[{"reference": "https://example.invalid/proof", "source_revision": "fixture", "environment": "test"}])
        db.insert(verification)

    @contextmanager
    def readiness():
        with DbSession.atomic() as db:
            yield SimpleNamespace(db=db, watch_card_and_dependents=lambda *_: None)

    monkeypatch.setattr(module, "execution_readiness_uow", readiness)
    approvals = Mock(return_value={})
    monkeypatch.setattr(module, "pending_card_approvals", approvals)
    monkeypatch.setattr(CardService, "next_change_seq", lambda _: 8)
    published = Mock()
    monkeypatch.setattr(module.CardPublisher, "order_changed", published)
    monkeypatch.setattr(CardService, "publish_work_states", lambda *_: None)
    tasks = [Mock(), Mock(), Mock()]
    monkeypatch.setattr(module.CardBotTask, "enqueue_card_moved_webhook", tasks[0])
    monkeypatch.setattr(module.CardBotTask, "card_moved", tasks[1])
    monkeypatch.setattr(module.CardActivityTask, "card_moved", tasks[2])
    return SimpleNamespace(service=DomainService().card, project=project, card=card, profile=profile,
                           grant=grant, stage=stage, verification=verification, now=now,
                           approvals=approvals, published=published, tasks=tasks)


def test_archive_uses_existing_move_and_one_profile_receipt(archive_command):
    f = archive_command
    assert f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, now=f.now) == {"status": "completed"}
    with DbSession.use(readonly=False) as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first()
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        runs = db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()
    assert column.is_archive and card.archived_at is not None and card.workflow_stage_entered_at is None
    assert len(runs) == 1
    run = runs[0]
    assert (run.actor_type, run.actor_id, run.profile_id, run.source, run.effects, run.result) == (
        "profile", f.profile.id, f.profile.id, "continuous", ["auto_archive"], {"archived": 1})
    assert f.service.archive_automatically(f.project.id, f.profile.id, f.card.id)["reason"] == "already_archived"
    f.published.assert_called_once()
    for task in f.tasks:
        task.assert_not_called()


@pytest.fixture
def scheduled_archive(archive_command):
    from langboard_shared.domain.models.WorkflowCompactionSchedule import WorkflowCompactionSchedule

    f = archive_command
    WorkflowCompactionSchedule.__table__.create(DbEngine.get_main_engine())
    with DbSession.atomic() as db:
        f.profile.is_default = True
        db.update(f.profile)
    return f


@pytest.mark.parametrize("compact", [False, True])
async def test_worker_archives_with_or_without_compaction_and_stops_rescanning(scheduled_archive, monkeypatch, compact):
    from langboard_shared.domain.models.WorkflowCompactionSchedule import WorkflowCompactionSchedule
    from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
    from langboard_shared.tasks.bots.WorkflowCompactionTask import run_due_compactions

    f = scheduled_archive
    with DbSession.atomic() as db:
        f.grant.can_compact = compact
        f.stage.continuous_effects = {**f.stage.continuous_effects, "auto_compact": compact}
        db.update(f.grant)
        db.update(f.stage)
    generate = AsyncMock(return_value={"status": "skipped", "reason": "below_threshold"})
    monkeypatch.setattr(CardCommentCompactionService, "generate_automatic", generate)
    assert await run_due_compactions(now=f.now) == 1
    assert generate.await_count == int(compact)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first().archived_at is not None
        assert len(db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()) == 1
        state = db.exec(SqlBuilder.select.table(WorkflowCompactionSchedule)).first()
        assert state.last_status == "completed" and state.lease_token is None
    assert await run_due_compactions(now=f.now + timedelta(hours=1)) == 0


async def test_worker_rechecks_archive_grant_after_scan(scheduled_archive, monkeypatch):
    from langboard_shared.tasks.bots import WorkflowCompactionTask as worker

    f = scheduled_archive
    original = worker._due

    def due(*args):
        rows = original(*args)
        with DbSession.atomic() as db:
            f.grant.can_archive = False
            db.update(f.grant)
        return rows

    monkeypatch.setattr(worker, "_due", due)
    assert await worker.run_due_compactions(now=f.now) == 0
    f.published.assert_not_called()


async def test_worker_preserves_interval_when_archive_is_not_verified(scheduled_archive):
    from langboard_shared.tasks.bots.WorkflowCompactionTask import run_due_compactions

    f = scheduled_archive
    with DbSession.atomic() as db:
        f.verification.decision = "partial"
        db.update(f.verification)
    assert await run_due_compactions(now=f.now) == 1
    assert await run_due_compactions(now=f.now + timedelta(seconds=59)) == 0
    f.published.assert_not_called()


async def test_worker_does_not_hide_failed_compaction_by_archiving(scheduled_archive, monkeypatch):
    from langboard_shared.domain.models.WorkflowCompactionSchedule import WorkflowCompactionSchedule
    from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
    from langboard_shared.tasks.bots.WorkflowCompactionTask import run_due_compactions

    f = scheduled_archive
    with DbSession.atomic() as db:
        f.grant.can_compact = True
        f.stage.continuous_effects = {**f.stage.continuous_effects, "auto_compact": True}
        db.update(f.grant)
        db.update(f.stage)
    generate = AsyncMock(return_value={"status": "failed", "error_code": "provider_unavailable"})
    monkeypatch.setattr(CardCommentCompactionService, "generate_automatic", generate)
    assert await run_due_compactions(now=f.now) == 1
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(WorkflowCompactionSchedule)).first().last_status == "failed"
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first().archived_at is None
    f.published.assert_not_called()


@pytest.mark.parametrize("change,reason", [
    ("delay", "not_due"), ("entry", "entry_evidence_missing"), ("policy", "policy_changed"),
    ("completion", "policy_changed"), ("verification", "verification_required"),
    ("revision", "verification_required"), ("approval", "approval_pending"),
    ("unfinished", "unfinished_work"), ("dependency", "dependency_pending"),
])
def test_archive_rechecks_current_native_gates(archive_command, change, reason):
    f = archive_command
    with DbSession.atomic() as db:
        if change == "delay":
            f.card.workflow_stage_entered_at = f.now
        elif change == "entry":
            f.card.workflow_stage_entered_at = None
        elif change == "policy":
            f.stage.continuous_effects = {"auto_archive": False}
        elif change == "completion":
            f.stage.counts_as_completed = False
        elif change == "verification":
            f.verification.decision = "partial"
        elif change == "revision":
            f.card.last_change_seq = 8
        elif change == "unfinished":
            checklist = Checklist(card_id=f.card.id, title="Unfinished")
            db.insert(checklist)
            db.insert(Checkitem(checklist_id=checklist.id, title="Still open", is_checked=False))
        elif change == "dependency":
            parent = Card(project_id=f.project.id, project_column_id=f.card.project_column_id, title="Prerequisite")
            db.insert(parent)
            relation = GlobalCardRelationshipType(parent_name="Blocks", child_name="Blocked", machine_semantic="blocks")
            db.insert(relation)
            # A completed stage alone would satisfy the dependency; make it unclassified.
            column = ProjectColumn(project_id=f.project.id, name="Unknown")
            db.insert(column)
            parent.project_column_id = column.id
            db.update(parent)
            db.insert(CardRelationship(card_id_parent=parent.id, card_id_child=f.card.id, relationship_type_id=relation.id))
        for row in (f.card, f.stage, f.verification):
            db.update(row)
    if change == "approval":
        f.approvals.return_value = {int(f.card.id): 1}
    assert f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, now=f.now) == {"status": "skipped", "reason": reason}
    f.published.assert_not_called()


@pytest.mark.parametrize("change", ["read", "archive", "private"])
def test_archive_never_borrows_user_authority(archive_command, change):
    f = archive_command
    with DbSession.atomic() as db:
        if change == "private":
            f.card.visibility = "PRIVATE"
            f.card.owner_user_id = f.grant.granted_by_user_id
            f.card.created_by_user_id = f.grant.granted_by_user_id
        else:
            setattr(f.grant, f"can_{change}", False)
            if change == "read":
                f.grant.can_archive = False
        db.update(f.card)
        db.update(f.grant)
    with pytest.raises(CompactorAuthorityError):
        f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, now=f.now)
    f.published.assert_not_called()


def test_archive_and_profile_receipt_roll_back_together(archive_command, monkeypatch):
    f = archive_command
    insert = DbSession.insert

    def fail_history(db, row, *args, **kwargs):
        if isinstance(row, WorkflowEffectRun):
            raise RuntimeError("receipt persistence failed")
        return insert(db, row, *args, **kwargs)

    monkeypatch.setattr(DbSession, "insert", fail_history)
    with pytest.raises(RuntimeError, match="receipt persistence" ):
        f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, now=f.now)
    with DbSession.use(readonly=False) as db:
        assert db.exec(select(Card.archived_at).where(Card.id == f.card.id)).first() == (None,)
    f.published.assert_not_called()


def test_archive_excludes_app_owned_cards(archive_command):
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied

    f = archive_command
    with DbSession.atomic() as db:
        db.insert(CardAppOwnership(card_id=f.card.id, app_key="github"))
    with pytest.raises(AppGovernanceDenied):
        f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, now=f.now)
    f.published.assert_not_called()


def test_concurrent_archive_has_one_movement_and_receipt(archive_command):
    from concurrent.futures import ThreadPoolExecutor

    f = archive_command
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("Row-lock serialization requires PostgreSQL")
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(f.service.archive_automatically, f.project.id, f.profile.id, f.card.id, now=f.now)
                   for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(result["status"] for result in results) == ["completed", "skipped"]
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()) == 1
    f.published.assert_called_once()
