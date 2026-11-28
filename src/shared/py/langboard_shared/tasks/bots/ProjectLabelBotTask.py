from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Project, ProjectLabel, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "project_label_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectLabelCreated, _create_schema())
@Broker.wrap_async_task_decorator
async def project_label_created(user_or_bot: User | Bot, project: Project, label: ProjectLabel):
    bots = BotTaskHelper.get_scoped_bots(DomainEventType.ProjectLabelCreated, project_id=project.id)
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectLabelCreated, create_label_data(user_or_bot, project, label), project
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectLabelUpdated, _create_schema())
@Broker.wrap_async_task_decorator
async def project_label_updated(user_or_bot: User | Bot, project: Project, label: ProjectLabel):
    bots = BotTaskHelper.get_scoped_bots(DomainEventType.ProjectLabelUpdated, project_id=project.id)
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectLabelUpdated, create_label_data(user_or_bot, project, label), project
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectLabelDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def project_label_deleted(user_or_bot: User | Bot, project: Project, label: ProjectLabel):
    bots = BotTaskHelper.get_scoped_bots(DomainEventType.ProjectLabelDeleted, project_id=project.id)
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectLabelDeleted, create_label_data(user_or_bot, project, label), project
    )


def create_label_data(
    user_or_bot: User | Bot, project: Project, label: ProjectLabel, other_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_project(user_or_bot, project),
        "project_label_uid": label.get_uid(),
        **(other_data or {}),
    }
