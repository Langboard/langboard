from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Card, Checkitem, Project, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "checkitem_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemCreated, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_created(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemCreated,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemTitleChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_title_changed(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemTitleChanged,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemTimerStarted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_timer_started(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemTimerStarted,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemTimerPaused, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_timer_paused(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemTimerPaused,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemTimerStopped, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_timer_stopped(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemTimerStopped,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemChecked, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_checked(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemChecked,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemUnchecked, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_unchecked(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemUnchecked,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


@BotTaskSchemaHelper.card_schema(
    DomainEventType.CardCheckitemCardified, _create_schema({"cardified_card_uid": "string"})
)
@Broker.wrap_async_task_decorator
async def card_checkitem_cardified(
    user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem, new_card: Card
):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemCardified,
        lambda: _create_data(user_or_bot, project, card, checkitem, {"cardified_card_uid": new_card.get_uid()}),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardCheckitemDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checkitem_deleted(user_or_bot: User | Bot, project: Project, card: Card, checkitem: Checkitem):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardCheckitemDeleted,
        lambda: _create_data(user_or_bot, project, card, checkitem),
    )


def _create_data(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    checkitem: Checkitem,
    other_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_card(user_or_bot, project, card),
        "checkitem_uid": checkitem.get_uid(),
        **(other_data or {}),
    }
