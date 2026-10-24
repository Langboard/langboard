from typing import Any
from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Project, ProjectWiki, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "project_wiki_uid": "string",
        **(other_schema or {}),
    }


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiCreated, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_created(user_or_bot: User | Bot, project: Project, wiki: ProjectWiki):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectWikiCreated, project_id=project.id, project_wiki_id=wiki.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectWikiCreated, create_wiki_data(user_or_bot, project, wiki), project
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiUpdated, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_updated(user_or_bot: User | Bot, project: Project, wiki: ProjectWiki):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectWikiUpdated, project_id=project.id, project_wiki_id=wiki.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectWikiUpdated, create_wiki_data(user_or_bot, project, wiki), project
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiPublicityChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_publicity_changed(user_or_bot: User | Bot, project: Project, wiki: ProjectWiki):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectWikiPublicityChanged, project_id=project.id, project_wiki_id=wiki.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectWikiPublicityChanged, create_wiki_data(user_or_bot, project, wiki), project
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_deleted(user_or_bot: User | Bot, project: Project, wiki: ProjectWiki):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectWikiDeleted, project_id=project.id, project_wiki_id=wiki.id
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectWikiDeleted, create_wiki_data(user_or_bot, project, wiki), project
    )


def create_wiki_data(
    user_or_bot: User | Bot, project: Project, wiki: ProjectWiki, other_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_project(user_or_bot, project),
        "project_wiki_uid": wiki.get_uid(),
        **(other_data or {}),
    }
