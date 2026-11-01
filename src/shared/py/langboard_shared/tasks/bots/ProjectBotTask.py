from ...core.broker import Broker
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Project, User
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectUpdated)
@Broker.wrap_async_task_decorator
async def project_updated(user_or_bot: User | Bot, project: Project):
    bots = BotTaskHelper.get_scoped_bots(DomainEventType.ProjectUpdated, project_id=project.id)
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectUpdated, BotTaskDataHelper.create_project(user_or_bot, project), project
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectDeleted)
@Broker.wrap_async_task_decorator
async def project_deleted(user: User, project: Project):
    bots = BotTaskHelper.get_scoped_bots(
        DomainEventType.ProjectDeleted,
        project_id=project.id,
        with_deleted_target=True,
    )
    await BotTaskHelper.run(
        bots, DomainEventType.ProjectDeleted, BotTaskDataHelper.create_project(user, project), project
    )
