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
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardChecklistCreated,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardChecklistCreated,
        _create_data(user_or_bot, project, card, checklist),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistTitleChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_title_changed(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardChecklistTitleChanged,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardChecklistTitleChanged,
        _create_data(user_or_bot, project, card, checklist),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistChecked, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_checked(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardChecklistChecked,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardChecklistChecked,
        _create_data(user_or_bot, project, card, checklist),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistUnchecked, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_unchecked(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardChecklistUnchecked,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardChecklistUnchecked,
        _create_data(user_or_bot, project, card, checklist),
        project,
    )


@BotTaskSchemaHelper.card_schema(DomainEventType.CardChecklistDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def card_checklist_deleted(user_or_bot: User | Bot, project: Project, card: Card, checklist: Checklist):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.CardChecklistDeleted,
        project_id=project.id,
        project_column_id=card.project_column_id,
        card_id=card.id,
    )
    await BotTaskHelper.run_card_event(
        user_or_bot, card,
        bots,
        DomainEventType.CardChecklistDeleted,
        _create_data(user_or_bot, project, card, checklist),
        project,
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
