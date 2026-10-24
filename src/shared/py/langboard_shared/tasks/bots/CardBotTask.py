from typing import Any
from ...core.broker import Broker
from ...domain.models import Bot, Card, Project, ProjectColumn, User
from ...domain.models.bases import BotTriggerCondition
from ..webhooks import WebhookTask
from ..webhooks.utils import WebhookModel
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardCreated)
@Broker.wrap_async_task_decorator
async def card_created(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardCreated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, BotTriggerCondition.CardCreated, BotTaskDataHelper.create_card(user_or_bot, project, card), project
    )


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardUpdated)
@Broker.wrap_async_task_decorator
async def card_updated(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, BotTriggerCondition.CardUpdated, BotTaskDataHelper.create_card(user_or_bot, project, card), project
    )


@BotTaskSchemaHelper.card_schema(
    BotTriggerCondition.CardMoved,
    {
        "old_project_column_uid": "string",
        "old_project_column_name": "string",
        "old_project_column_is_archive": "boolean",
    },
)
@Broker.wrap_async_task_decorator
async def card_moved(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    old_column: ProjectColumn,
    emit_webhook: bool,
) -> None:
    await _run_card_moved(user_or_bot, project, card, old_column, emit_webhook)


async def _run_card_moved(user_or_bot, project, card, old_column, emit_webhook=False):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardMoved, project_id=project.id, project_column_id=card.project_column_id, card_id=card.id
    )
    data = create_card_moved_data(user_or_bot, project, card, old_column)
    if old_column.workflow_stage and old_column.workflow_stage == card.workflow_stage_entered_key:
        selected = []
    else:
        selected = await _run_workflow_entry_bots(user_or_bot, card, data)
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        [item for item in bots if item[0].get_uid() not in selected],
        BotTriggerCondition.CardMoved,
        data,
        project,
        emit_webhook=emit_webhook,
    )


async def _run_workflow_entry_bots(actor, card, data):
    from ...core.db import DbSession, SqlBuilder
    from ...core.types import SafeDateTime
    from ...domain.models import WorkflowStageDefinition
    from .WorkflowCompactionTask import _claim_workflow_bot, _dispatch_workflow_bot, _utc

    if not isinstance(actor, User) or not card.workflow_stage_entered_key or not card.workflow_stage_entered_at:
        return []
    with DbSession.use(readonly=False) as db:
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
            WorkflowStageDefinition.key == card.workflow_stage_entered_key,
        )).first()
        bindings = list(stage.entry_bots) if stage and stage.is_active else []
    selected = []
    for binding in bindings:
        claim = _claim_workflow_bot(stage.id, card.id, binding, "bot_entry", SafeDateTime.now(),
                                    actor_id=actor.id, residency=_utc(card.workflow_stage_entered_at).isoformat())
        selected.append(binding["bot_uid"])
        if claim:
            await _dispatch_workflow_bot(claim, binding, data=data)
    return selected


def enqueue_card_moved_webhook(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    old_column: ProjectColumn,
    current_column: ProjectColumn,
) -> None:
    """Freeze and queue a move webhook before optional bot execution."""

    data = create_card_moved_data(user_or_bot, project, card, old_column, current_column)
    WebhookTask.webhook_task(WebhookModel(event=BotTriggerCondition.CardMoved.value, data=data))


def create_card_moved_data(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    old_column: ProjectColumn,
    current_column: ProjectColumn | None = None,
) -> dict[str, Any]:
    """Create the stable card-move snapshot shared by webhooks and bots."""

    return {
        **BotTaskDataHelper.create_card(user_or_bot, project, card, current_column),
        "old_project_column_uid": old_column.get_uid(),
        "old_project_column_name": old_column.name,
        "old_project_column_is_archive": old_column.is_archive,
    }


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardLabelsUpdated)
@Broker.wrap_async_task_decorator
async def card_labels_updated(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardLabelsUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        BotTriggerCondition.CardLabelsUpdated,
        BotTaskDataHelper.create_card(user_or_bot, project, card),
        project,
    )


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardRelationshipsUpdated)
@Broker.wrap_async_task_decorator
async def card_relationship_updated(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardRelationshipsUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        BotTriggerCondition.CardRelationshipsUpdated,
        BotTaskDataHelper.create_card(user_or_bot, project, card),
        project,
    )


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardDeleted)
@Broker.wrap_async_task_decorator
async def card_deleted(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardDeleted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, BotTriggerCondition.CardDeleted, BotTaskDataHelper.create_card(user_or_bot, project, card), project
    )
