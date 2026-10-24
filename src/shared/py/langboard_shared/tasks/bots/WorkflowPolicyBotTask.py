"""Stage policy selection uses native card hooks and their current configured conditions."""

from uuid import uuid4
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.models import User, WorkflowStageDefinition
from .WorkflowCompactionTask import _claim_workflow_bot, _dispatch_workflow_bot, _utc


async def run_policy_bots(actor, card, event, data, project, candidates):
    from .utils import BotTaskHelper

    if (not isinstance(actor, User) or not card.workflow_stage_entered_key
            or card.workflow_stage_entered_at is None or BotTaskHelper.is_bot_authored_event(data)):
        return set()
    # The event's card snapshot fences queued events across moves and re-entry.
    residency = _utc(card.workflow_stage_entered_at).isoformat()
    with DbSession.use(readonly=False) as db:
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
            WorkflowStageDefinition.key == card.workflow_stage_entered_key,
        ).execution_options(populate_existing=True)).first()
        bindings = list(stage.policy_bots) if stage and stage.is_active else []
    selected = set()
    event_key = f"policy:{residency.replace(':', '_')}:{uuid4().hex}"
    for binding in bindings:
        if binding["bot_uid"] not in candidates:
            continue
        selected.add(binding["bot_uid"])
        claim = _claim_workflow_bot(stage.id, card.id, binding, "bot_policy", SafeDateTime.now(),
                                    actor_id=actor.id, residency=residency, condition=event.value,
                                    event_key=f"{event_key}:{binding['bot_uid']}")
        if claim:
            await _dispatch_workflow_bot(claim, binding, data=data)
    return selected
