from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Project, ProjectColumn, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


@BotTaskSchemaHelper.project_column_schema(DomainEventType.ProjectColumnCreated)
@Broker.wrap_async_task_decorator
async def project_column_created(user_or_bot: User | Bot, project: Project, column: ProjectColumn):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectColumnCreated, project_id=project.id, project_column_id=column.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectColumnCreated, create_column_data(user_or_bot, project, column), project
    )


@BotTaskSchemaHelper.project_column_schema(DomainEventType.ProjectColumnNameChanged)
@Broker.wrap_async_task_decorator
async def project_column_name_changed(user_or_bot: User | Bot, project: Project, column: ProjectColumn):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectColumnNameChanged, project_id=project.id, project_column_id=column.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectColumnNameChanged, create_column_data(user_or_bot, project, column), project
    )


@BotTaskSchemaHelper.project_column_schema(DomainEventType.ProjectColumnDeleted)
@Broker.wrap_async_task_decorator
async def project_column_deleted(user_or_bot: User | Bot, project: Project, column: ProjectColumn):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectColumnDeleted, project_id=project.id, project_column_id=column.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectColumnDeleted, create_column_data(user_or_bot, project, column), project
    )


def create_column_data(
    user_or_bot: User | Bot,
    project: Project,
    column: ProjectColumn,
    other_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_project_column(user_or_bot, project, column),
        **(other_data or {}),
    }
