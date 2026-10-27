"""Automatic summaries belong to the stage residency that authorized the request."""

from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardCommentCompaction,
    WorkflowActionRun,
    WorkflowEffectRun,
    WorkflowRuleRun,
)
from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
from langboard_shared.domain.services.factory.WorkflowRuleService import rows
from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService
from src.api.tests.model_providers.conftest import compactions  # noqa: F401
from src.api.tests.model_providers.test_comment_compaction_command import (
    automatic_command,  # noqa: F401
    command,
    compact_command,  # noqa: F401
    draft,
)
from src.api.tests.model_providers.workflow_rule_fixtures import configure_rule, rule_id


@pytest.mark.parametrize("reenter", [False, True])
async def test_automatic_result_rechecks_stage_residency(automatic_command, board, monkeypatch, reenter):  # noqa: F811
    (service, actor, project, card, binding, *_), column, _ = automatic_command
    policy = service._get_service(WorkflowStagePolicyService)
    other_column = board[5][1]
    with DbSession.atomic() as db:
        card.workflow_stage_entered_key = column.workflow_stage
        card.workflow_stage_entered_at = SafeDateTime.now() - timedelta(minutes=5)
        db.update(card)

    async def generate(_, snapshot):
        if reenter:
            # Use the native transition boundary: leave, then return to the same
            # column and definition before the asynchronous model result arrives.
            with DbSession.atomic() as db:
                card.project_column_id = other_column.id
                db.update(card)
                policy.apply_transition(actor, project, card, column, other_column)
            with DbSession.atomic() as db:
                card.project_column_id = column.id
                db.update(card)
                policy.apply_transition(actor, project, card, other_column, column)
        return draft(snapshot)

    monkeypatch.setattr(command, "request_compaction_draft", generate)
    result = await service.generate_automatic(project, binding, card, rule_id=rule_id(automatic_command[2]))
    with DbSession.use(readonly=False) as db:
        record = db.exec(SqlBuilder.select.table(CardCommentCompaction)).first()
    if reenter:
        assert result["status"] == "failed" and result["error_code"] == "permission_changed"
        assert record.draft is None
    else:
        assert result["status"] == "completed" and record.draft is not None


@pytest.mark.parametrize("change", ["unchanged", "reentry", "stage_revision", "rule_revision"])
@pytest.mark.parametrize("phase", ["claim", "inference"])
async def test_worker_compaction_preserves_origin_after_claim(automatic_command, board, monkeypatch, change, phase):  # noqa: F811
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    (service, actor, project, card, binding, *_), column, stage = automatic_command
    policy = service._get_service(WorkflowStagePolicyService)

    async def generate_draft(_, snapshot):
        if phase == "inference":
            change_context()
        return draft(snapshot)

    generate = AsyncMock(side_effect=generate_draft)
    monkeypatch.setattr(command, "request_compaction_draft", generate)
    original = CardCommentCompactionService.generate_automatic
    dispatched = []

    def change_context():
        if change == "reentry":
            other = board[5][1]
            for old, new in ((column, other), (other, column)):
                with DbSession.atomic() as db:
                    card.project_column_id = new.id
                    db.update(card)
                    policy.apply_transition(actor, project, card, old, new)
        elif change in {"stage_revision", "rule_revision"}:
            with DbSession.atomic() as db:
                if change == "stage_revision":
                    stage.description = "Edited after action claim"
                    db.update(stage)
                else:
                    configure_rule(db, stage, interval=301)

    async def after_claim(compaction, *args, **kwargs):
        assert not DbSession.has_active_transaction()
        with DbSession.use(readonly=False) as db:
            origin = rows(db, WorkflowRuleRun)[0]
            claim = rows(db, WorkflowActionRun)[0]
            assert origin.status == claim.status == "running" and claim.attempt == 1
        dispatched.append(origin.id)
        if phase == "claim":
            change_context()
        return await original(compaction, *args, **kwargs)

    monkeypatch.setattr(CardCommentCompactionService, "generate_automatic", after_claim)
    assert await run_due_rules() == 1
    assert len(dispatched) == 1
    stale = change != "unchanged"
    reserved = not stale or phase == "inference"
    assert generate.await_count == int(reserved)
    with DbSession.use(readonly=False) as db:
        run, claim = rows(db, WorkflowRuleRun)[0], rows(db, WorkflowActionRun)[0]
        expected = "skipped" if stale and phase == "claim" else "failed" if stale else "succeeded"
        assert run.status == claim.status == expected
        assert claim.attempt == 1 and claim.lease_expires_at is None
        if stale and phase == "claim":
            assert claim.result == {"status": "skipped", "reason": "workflow_context_changed"}
        records = rows(db, CardCommentCompaction)
        assert len(records) == int(reserved)
        if stale and phase == "inference":
            assert claim.result["error_code"] == records[0].error_code == "permission_changed"
            assert records[0].draft is None
        effects = rows(db, WorkflowEffectRun)
        assert len(effects) == int(reserved)
        if reserved:
            assert effects[0].status == expected
        assert rows(db, Card, id=card.id)[0].archived_at is None
