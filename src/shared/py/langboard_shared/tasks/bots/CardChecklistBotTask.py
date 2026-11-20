from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Card, Checklist, Project, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "checklist_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistCreated, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_created(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardChecklistCreated,
        lambda: _create_data(user_or_bot, project, card, checklist),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistTitleChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_title_changed(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardChecklistTitleChanged,
        lambda: _create_data(user_or_bot, project, card, checklist),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistChecked, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_checked(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardChecklistChecked,
        lambda: _create_data(user_or_bot, project, card, checklist),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistUnchecked, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_unchecked(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardChecklistUnchecked,
        lambda: _create_data(user_or_bot, project, card, checklist),
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_deleted(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    await BotTaskHelper.run_scoped_card_event(
        user_or_bot,
        project,
        card,
        DomainEventType.CardChecklistDeleted,
        lambda: _create_data(user_or_bot, project, card, checklist),
    )


def _create_data(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    checklist: Checklist,
    other_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_card(user_or_bot, project, card),
        "checklist_uid": checklist.get_uid(),
        **(other_data or {}),
    }
