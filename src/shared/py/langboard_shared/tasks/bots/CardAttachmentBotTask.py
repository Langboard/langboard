from typing import Any
from ...core.broker import Broker
from ...domain.models import Bot, Card, CardAttachment, Project, User
from ...domain.models.bases import BotTriggerCondition
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "attachment_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardAttachmentUploaded, _create_schema())
@Broker.wrap_async_task_decorator
async def card_attachment_uploaded(user_or_bot: User | Bot, project: Project, card: Card, attachment: CardAttachment):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardAttachmentUploaded,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        BotTriggerCondition.CardAttachmentUploaded,
        _create_data(user_or_bot, project, card, attachment),
        project,
    )


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardAttachmentNameChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def card_attachment_name_changed(
    user_or_bot: User | Bot, project: Project, card: Card, attachment: CardAttachment
):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardAttachmentNameChanged,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        BotTriggerCondition.CardAttachmentNameChanged,
        _create_data(user_or_bot, project, card, attachment),
        project,
    )


@BotTaskSchemaHelper.card_schema(BotTriggerCondition.CardAttachmentDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_attachment_deleted(user_or_bot: User | Bot, project: Project, card: Card, attachment: CardAttachment):
    bots = BotTaskHelper.get_scoped_bots(
        BotTriggerCondition.CardAttachmentDeleted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        BotTriggerCondition.CardAttachmentDeleted,
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
