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
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCommentAdded,
        lambda: _create_data(user_or_bot, project, card, comment),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentUpdated, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_updated(user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCommentUpdated,
        lambda: _create_data(user_or_bot, project, card, comment),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_deleted(user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCommentDeleted,
        lambda: _create_data(user_or_bot, project, card, comment),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentReacted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_reacted(
    user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment, reaction: str
):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCommentReacted,
        lambda: _create_data(user_or_bot, project, card, comment, {"reaction_type": reaction}),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCommentUnreacted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_comment_unreacted(
    user_or_bot: User | Bot, project: Project, card: Card, comment: CardComment, reaction: str
):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCommentUnreacted,
        lambda: _create_data(user_or_bot, project, card, comment, {"reaction_type": reaction}),
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
