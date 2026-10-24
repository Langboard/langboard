from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Card, CardComment, Project, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "comment_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentAdded, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_added(user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardCommentAdded,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, DomainEventType.CardCommentAdded, _create_data(user_or_bot, project, card, comment), project
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentUpdated, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_updated(user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardCommentUpdated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, DomainEventType.CardCommentUpdated, _create_data(user_or_bot, project, card, comment), project
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_deleted(user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardCommentDeleted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots, DomainEventType.CardCommentDeleted, _create_data(user_or_bot, project, card, comment), project
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentReacted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_reacted(
    user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment, reaction: str
):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardCommentReacted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardCommentReacted,
        _create_data(user_or_bot, project, card, comment, {"reaction_type": reaction}),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentUnreacted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_unreacted(
    user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment, reaction: str
):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardCommentUnreacted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardCommentUnreacted,
        _create_data(user_or_bot, project, card, comment, {"reaction_type": reaction}),
        project,
    )


def _create_data(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    comment: CardComment,
    other_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_card(user_or_bot, project, card),
        "comment_uid": comment.get_uid(),
        **(other_data or {}),
    }
