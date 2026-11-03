from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Card, CardAttachment, Project, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "attachment_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.card_schema(DomainEventType.CardAttachmentUploaded, _create_schema())
@Broker.wrap_async_task_decorator
async def card_attachment_uploaded(user_or_bot: User | Bot, project: Project, card: Card, attachment: CardAttachment):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardAttachmentUploaded,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardAttachmentUploaded,
        _create_data(user_or_bot, project, card, attachment),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardAttachmentNameChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def card_attachment_name_changed(
    user_or_bot: User | Bot, project: Project, card: Card, attachment: CardAttachment
):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardAttachmentNameChanged,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardAttachmentNameChanged,
        _create_data(user_or_bot, project, card, attachment),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardAttachmentDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_attachment_deleted(user_or_bot: User | Bot, project: Project, card: Card, attachment: CardAttachment):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardAttachmentDeleted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardAttachmentDeleted,
        _create_data(user_or_bot, project, card, attachment),
        project,
    )


def _create_data(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    attachment: CardAttachment,
    other_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_card(user_or_bot, project, card),
        "attachment_uid": attachment.get_uid(),
        **(other_data or {}),
    }
