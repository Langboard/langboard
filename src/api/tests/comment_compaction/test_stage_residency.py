"""Automatic summaries belong to the stage residency that authorized the request."""

from datetime import timedelta
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import CardCommentCompaction
from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService
from src.api.tests.model_providers.workflow_rule_fixtures import rule_id
from src.api.tests.model_providers.conftest import compactions  # noqa: F401
from src.api.tests.model_providers.test_comment_compaction_command import (
    automatic_command,  # noqa: F401
    command,
    compact_command,  # noqa: F401
    draft,
)


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
