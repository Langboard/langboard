from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Card, Project, ProjectColumn, User
from ..webhooks import WebhookTask
from ..webhooks.utils import WebhookModel
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCreated)
@Broker.wrap_async_task_decorator
async def card_created(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardCreated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, DomainEventType.CardCreated, BotTaskDataHelper.create_card(user_or_bot, project, card), project
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardUpdated)
@Broker.wrap_async_task_decorator
async def card_updated(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, DomainEventType.CardUpdated, BotTaskDataHelper.create_card(user_or_bot, project, card), project
    )


@BotTaskSchemaHelper.card_schema(
    DomainEventType.CardMoved,
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
        DomainEventType.CardMoved, project_id=project.id, project_column_id=card.project_column_id, card_id=card.id
    )
    data = create_card_moved_data(user_or_bot, project, card, old_column)
    if old_column.workflow_stage and old_column.workflow_stage == card.workflow_stage_entered_key:
        selected = []
    else:
        selected = await _run_workflow_entry_bots(user_or_bot, card, data)
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        [item for item in bots if item[0].get_uid() not in selected],
        DomainEventType.CardMoved,
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
    WebhookTask.webhook_task(WebhookModel(event=DomainEventType.CardMoved.value, data=data))


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


@BotTaskSchemaHelper.card_schema(DomainEventType.CardLabelsUpdated)
@Broker.wrap_async_task_decorator
async def card_labels_updated(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardLabelsUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardLabelsUpdated,
        BotTaskDataHelper.create_card(user_or_bot, project, card),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardRelationshipsUpdated)
@Broker.wrap_async_task_decorator
async def card_relationship_updated(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardRelationshipsUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardRelationshipsUpdated,
        BotTaskDataHelper.create_card(user_or_bot, project, card),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardDeleted)
@Broker.wrap_async_task_decorator
async def card_deleted(user_or_bot: User | Bot, project: Project, card: Card):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardDeleted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, DomainEventType.CardDeleted, BotTaskDataHelper.create_card(user_or_bot, project, card), project
    )
