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
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardMoved,
        data,
        project,
        emit_webhook=emit_webhook,
    )


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
