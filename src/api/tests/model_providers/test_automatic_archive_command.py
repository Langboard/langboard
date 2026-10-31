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
    CardChangeReceipt,
    CardRelationship,
    CardVerificationRecord,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    ProjectActivity,
    ProjectColumn,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CompactorBoardAuthority import CompactorAuthorityError
from langboard_shared.domain.services.factory.CardService import CardService
from sqlalchemy import select
from src.api.tests.model_providers.workflow_rule_fixtures import configure_rule, create_rule_tables, rule_id


module = importlib.import_module(CardService.__module__)


@pytest.fixture
def archive_command(compactions, board, monkeypatch):
    _, actor, card, profile, *_ = compactions
    project = board[2]
    for model in (CardAppOwnership, CardChangeReceipt, GlobalCardRelationshipType, CardRelationship, CardVerificationRecord,
                  Checklist, Checkitem, CompactorBoardGrant, ProjectActivity):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    create_rule_tables()
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        column.workflow_stage = "archive_ready"
        db.update(column)
        stage = WorkflowStageDefinition(key="archive_ready", name="Verified work", counts_as_completed=True)
        db.insert(stage)
        configure_rule(db, stage, compact=False, archive=True)
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
                           grant=grant, stage=stage, rule_id=rule_id(stage), verification=verification, now=now,
                           approvals=approvals, published=published, tasks=tasks)


def test_archive_uses_existing_move_and_one_profile_receipt(archive_command):
    f = archive_command
    result = f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id, now=f.now)
    assert result["status"] == "completed"
    with DbSession.use(readonly=False) as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first()
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        runs = db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()
        changes = db.exec(SqlBuilder.select.table(CardChangeReceipt)).all()
    assert column.is_archive and card.archived_at is not None and card.workflow_stage_entered_at is None
    assert len(runs) == 1
    assert [(change.card_id, change.change_seq) for change in changes] == [(f.card.id, 8)]
    run = runs[0]
    assert result["legacy_effect_run_uid"] == run.get_uid()
    assert (run.actor_type, run.actor_id, run.profile_id, run.source, run.effects, run.result) == (
        "profile", f.profile.id, f.profile.id, "continuous", ["auto_archive"], {"archived": 1})
    assert f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id)["reason"] == "already_archived"
    f.published.assert_called_once()
    for task in f.tasks:
        task.assert_not_called()


@pytest.fixture
def scheduled_archive(archive_command):
    f = archive_command
    with DbSession.atomic() as db:
        f.profile.is_default = True
        db.update(f.profile)
    return f


def archive_runs():
    from langboard_shared.domain.models import WorkflowActionRun, WorkflowRuleRun
    with DbSession.use(readonly=False) as db:
        return (db.exec(SqlBuilder.select.table(WorkflowRuleRun)).all(),
                db.exec(SqlBuilder.select.table(WorkflowActionRun).order_by(WorkflowActionRun.id)).all())


@pytest.mark.parametrize("compact", [False, True])
async def test_worker_archives_with_or_without_compaction_and_stops_rescanning(scheduled_archive, monkeypatch, compact):
    from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = scheduled_archive
    with DbSession.atomic() as db:
        f.grant.can_compact = compact
        configure_rule(db, f.stage, compact=compact, archive=True, characters=1)
        db.update(f.grant)
    generate = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(CardCommentCompactionService, "generate_automatic", generate)
    assert await run_due_rules(now=f.now) == 1
    assert generate.await_count == int(compact)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first().archived_at is not None
        assert len(db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()) == 1
    runs, claims = archive_runs()
    assert runs[0].status == "succeeded"
    assert len(claims) == 1 + int(compact)
    assert all(claim.status == "succeeded" and claim.lease_expires_at is None for claim in claims)
    assert all(claim.executor_id == f.profile.id and claim.attempt == 1 for claim in claims)
    assert await run_due_rules(now=f.now + timedelta(hours=1)) == 0


async def test_worker_rechecks_archive_grant_after_scan(scheduled_archive, monkeypatch):
    from langboard_shared.tasks import WorkflowRuleTask as worker

    f = scheduled_archive
    original = worker._admit_intervals

    def admit(*args):
        count = original(*args)
        with DbSession.atomic() as db:
            f.grant.can_archive = False
            db.update(f.grant)
        return count

    monkeypatch.setattr(worker, "_admit_intervals", admit)
    assert await worker.run_due_rules(now=f.now) == 1
    assert archive_runs()[0][0].status == "skipped"
    assert archive_runs()[1][0].attempt == 0
    f.published.assert_not_called()


@pytest.mark.parametrize("change", ["unchanged", "reentry", "stage_revision", "rule_revision"])
async def test_worker_archive_preserves_origin_after_claim(scheduled_archive, board, monkeypatch, change):
    """Fresh eligibility cannot authorize an action claimed by an older run."""
    from itertools import count
    from langboard_shared.domain.models import CardDecisionGate, DecisionCase, WorkflowRule, WorkflowRuleRun
    from langboard_shared.domain.services.CardVerification import VerificationSubmission
    from langboard_shared.domain.services.factory.WorkflowRuleService import rows, utc
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = scheduled_archive
    actor, other = board[1], board[5][1]
    for model in (DecisionCase, CardDecisionGate):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.atomic() as db:
        configure_rule(db, f.stage, compact=False, archive=True, archive_after_seconds=0)
        origin_column = rows(db, ProjectColumn, id=f.card.project_column_id)[0]
    original = CardService.archive_automatically
    dispatched = []
    sequences = count(8)
    monkeypatch.setattr(CardService, "next_change_seq", lambda _: next(sequences))
    monkeypatch.setattr(CardService, "get_work_states", lambda _, cards: {card.id: {} for card in cards})
    monkeypatch.setattr(module.CardPublisher, "updated", Mock())

    def after_claim(service, *args, **kwargs):
        assert not DbSession.has_active_transaction()
        runs, claims = archive_runs()
        assert len(runs) == len(claims) == 1
        origin, claim = runs[0], claims[0]
        assert origin.status == claim.status == "running" and claim.attempt == 1
        dispatched.append(origin.id)
        if change == "reentry":
            assert service._change_order(actor, f.project, f.card, 0, other)
            assert service._change_order(actor, f.project, f.card, 0, origin_column)
            with DbSession.use(readonly=False) as db:
                current = rows(db, Card, id=f.card.id)[0]
            verification = service.record_verification_evidence(actor, f.project, current, VerificationSubmission(
                expected_change_seq=current.last_change_seq,
                expected_record_uid=f.verification.get_uid(), decision="verified",
                evidence=f.verification.evidence,
            ))
            assert verification["source_change_seq"] == current.last_change_seq == 9
            assert utc(current.workflow_stage_entered_at).isoformat() != origin.residency
            assert current.workflow_stage_entered_key == f.stage.key
        elif change in {"stage_revision", "rule_revision"}:
            with DbSession.atomic() as db:
                if change == "stage_revision":
                    f.stage.description = "Edited after action claim"
                    db.update(f.stage)
                else:
                    configure_rule(db, f.stage, compact=False, archive=True, archive_after_seconds=0, interval=301)
                    assert rows(db, WorkflowRule, id=f.rule_id)[0].revision != origin.rule_revision
        f.published.reset_mock()
        for task in f.tasks:
            task.reset_mock()
        return original(service, *args, **kwargs)

    monkeypatch.setattr(CardService, "archive_automatically", after_claim)
    assert await run_due_rules(now=f.now) == 1
    assert len(dispatched) == 1
    runs, claims = archive_runs()
    stale = change != "unchanged"
    assert runs[0].status == claims[0].status == ("skipped" if stale else "succeeded")
    assert claims[0].attempt == 1 and claims[0].lease_expires_at is None
    if stale:
        assert claims[0].result == {"status": "skipped", "reason": "workflow_context_changed"}
    with DbSession.use(readonly=False) as db:
        card = rows(db, Card, id=f.card.id)[0]
        assert (card.archived_at is not None) == (not stale)
        assert len(rows(db, WorkflowEffectRun)) == int(not stale)
        assert len(rows(db, CardChangeReceipt)) == (2 if change == "reentry" else int(not stale))
        assert rows(db, WorkflowRuleRun, id=runs[0].id)[0].residency == runs[0].residency
    if stale:
        f.published.assert_not_called()
    else:
        f.published.assert_called_once()
    for task in f.tasks:
        task.assert_not_called()


async def test_worker_preserves_interval_when_archive_is_not_verified(scheduled_archive):
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = scheduled_archive
    with DbSession.atomic() as db:
        f.verification.decision = "partial"
        db.update(f.verification)
    assert await run_due_rules(now=f.now) == 1
    assert await run_due_rules(now=f.now + timedelta(seconds=59)) == 0
    runs, claims = archive_runs()
    assert runs[0].status == "skipped" and claims[0].result == {"status": "skipped", "reason": "verification_required"}
    f.published.assert_not_called()


@pytest.mark.parametrize("status", ["failed", "skipped"])
async def test_worker_does_not_hide_failed_compaction_by_archiving(scheduled_archive, monkeypatch, status):
    from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = scheduled_archive
    with DbSession.atomic() as db:
        f.grant.can_compact = True
        configure_rule(db, f.stage, compact=True, archive=True, characters=1)
        db.update(f.grant)
    result = {"status": status, **({"error_code": "provider_unavailable"} if status == "failed" else {"reason": "below_threshold"})}
    generate = AsyncMock(return_value=result)
    monkeypatch.setattr(CardCommentCompactionService, "generate_automatic", generate)
    assert await run_due_rules(now=f.now) == 1
    runs, claims = archive_runs()
    assert runs[0].status == status
    by_kind = {claim.kind: claim for claim in claims}
    assert by_kind["comments.compact"].status == status
    assert by_kind["card.archive"].status == "skipped"
    assert by_kind["card.archive"].error_code == "predecessor_not_succeeded"
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == f.card.id)).first().archived_at is None
    f.published.assert_not_called()


@pytest.mark.parametrize("change,reason", [
    ("delay", "not_due"), ("entry", "entry_evidence_missing"), ("policy", "policy_changed"),
    ("completion", "policy_changed"), ("verification", "verification_required"),
    ("revision", "verification_required"), ("approval", "approval_pending"),
    ("unfinished", "unfinished_work"), ("timer_started", "unfinished_work"),
    ("timer_paused", "unfinished_work"), ("dependency", "dependency_pending"),
])
def test_archive_rechecks_current_native_gates(archive_command, change, reason):
    f = archive_command
    with DbSession.atomic() as db:
        if change == "delay":
            f.card.workflow_stage_entered_at = f.now
        elif change == "entry":
            f.card.workflow_stage_entered_at = None
        elif change == "policy":
            configure_rule(db, f.stage, compact=False, archive=True, enabled=False)
        elif change == "completion":
            f.stage.counts_as_completed = False
        elif change == "verification":
            f.verification.decision = "partial"
        elif change == "revision":
            f.card.last_change_seq = 8
        elif change in {"timer_started", "timer_paused"}:
            from langboard_shared.domain.models.Checkitem import CheckitemStatus
            checklist = Checklist(card_id=f.card.id, title="Completed with active timer")
            db.insert(checklist)
            db.insert(Checkitem(checklist_id=checklist.id, title="Checked work", is_checked=True, user_id=f.grant.granted_by_user_id,
                                status=CheckitemStatus.Started if change == "timer_started" else CheckitemStatus.Paused))
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
    assert f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id, now=f.now) == {"status": "skipped", "reason": reason}
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
        f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id, now=f.now)
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
        f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id, now=f.now)
    with DbSession.use(readonly=False) as db:
        assert db.exec(select(Card.archived_at).where(Card.id == f.card.id)).first() == (None,)
        assert db.exec(SqlBuilder.select.table(CardChangeReceipt)).all() == []
    f.published.assert_not_called()


def test_archive_excludes_app_owned_cards(archive_command):
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied

    f = archive_command
    with DbSession.atomic() as db:
        db.insert(CardAppOwnership(card_id=f.card.id, app_key="github"))
    with pytest.raises(AppGovernanceDenied):
        f.service.archive_automatically(f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id, now=f.now)
    f.published.assert_not_called()


def test_concurrent_archive_has_one_movement_and_receipt(archive_command):
    from concurrent.futures import ThreadPoolExecutor

    f = archive_command
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("Row-lock serialization requires PostgreSQL")
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(f.service.archive_automatically, f.project.id, f.profile.id, f.card.id, rule_id=f.rule_id, now=f.now)
                   for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(result["status"] for result in results) == ["completed", "skipped"]
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()) == 1
    f.published.assert_called_once()
