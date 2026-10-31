from typing import Any
from sqlalchemy.orm import defer
from ...core.broker import Broker
from ...core.db import DbSession, SqlBuilder
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Project, ProjectAssignedUser, ProjectWiki, ProjectWikiAssignedUser, User
from ...helpers import InfraHelper
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


def _create_schema(other_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "project_wiki_uid": "string",
        **(other_schema or {}),
    }


def wiki_event_args(user_or_bot: User | Bot, project: Project, wiki: ProjectWiki) -> tuple[str, str, str, str, int]:
    """The broker receives identifiers and a generation, never a wiki body snapshot."""
    return (
        "user" if isinstance(user_or_bot, User) else "bot",
        user_or_bot.get_uid(),
        project.get_uid(),
        wiki.get_uid(),
        wiki.editor_generation,
    )


async def _run_wiki_event(event, actor_kind, actor_uid, project_uid, wiki_uid, generation):
    actor_model = {"user": User, "bot": Bot}.get(actor_kind)
    if actor_model is None or type(generation) is not int or generation < 0:
        return
    try:
        actor_id, project_id, wiki_id = [InfraHelper.convert_id(uid) for uid in (actor_uid, project_uid, wiki_uid)]
    except (TypeError, ValueError):
        return
    with DbSession.atomic() as db:
        actor = db.exec(SqlBuilder.select.table(actor_model).where(actor_model.id == actor_id)).first()
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == project_id)).first()
        wiki = db.exec(
            SqlBuilder.select.table(
                ProjectWiki,
                with_deleted=event == DomainEventType.ProjectWikiDeleted,
            )
            .where(ProjectWiki.id == wiki_id, ProjectWiki.project_id == project_id)
            .options(defer(ProjectWiki.content, raiseload=True))
            .with_for_update()
        ).first()
        if actor is None or project is None or wiki is None or wiki.editor_generation != generation:
            return
        if isinstance(actor, User):
            if not actor.activated_at:
                return
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id,
                    ProjectAssignedUser.user_id == actor.id,
                )
            ).first()
            if not actor.is_admin and project.owner_id != actor.id and member is None:
                return
            assigned = db.exec(
                SqlBuilder.select.table(ProjectWikiAssignedUser).where(
                    ProjectWikiAssignedUser.project_wiki_id == wiki.id,
                    ProjectWikiAssignedUser.user_id == actor.id,
                )
            ).first()
            if not wiki.is_public and not actor.is_admin and project.owner_id != actor.id and assigned is None:
                return
        data = create_wiki_data(actor, project, wiki)
        bots = BotTaskHelper.get_scoped_bots(event, project_id=project.id, project_wiki_id=wiki.id)
    await BotTaskHelper.run(bots, event, data, project)


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiCreated, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_created(actor_kind: str, actor_uid: str, project_uid: str, wiki_uid: str, generation: int):
    await _run_wiki_event(DomainEventType.ProjectWikiCreated, actor_kind, actor_uid, project_uid, wiki_uid, generation)


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiUpdated, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_updated(actor_kind: str, actor_uid: str, project_uid: str, wiki_uid: str, generation: int):
    await _run_wiki_event(DomainEventType.ProjectWikiUpdated, actor_kind, actor_uid, project_uid, wiki_uid, generation)


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiPublicityChanged, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_publicity_changed(
    actor_kind: str, actor_uid: str, project_uid: str, wiki_uid: str, generation: int
):
    await _run_wiki_event(
        DomainEventType.ProjectWikiPublicityChanged, actor_kind, actor_uid, project_uid, wiki_uid, generation
    )


@BotTaskSchemaHelper.project_schema(DomainEventType.ProjectWikiDeleted, _create_schema())
@Broker.wrap_async_task_decorator
async def project_wiki_deleted(actor_kind: str, actor_uid: str, project_uid: str, wiki_uid: str, generation: int):
    await _run_wiki_event(DomainEventType.ProjectWikiDeleted, actor_kind, actor_uid, project_uid, wiki_uid, generation)


def create_wiki_data(
    user_or_bot: User | Bot, project: Project, wiki: ProjectWiki, other_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **BotTaskDataHelper.create_project(user_or_bot, project),
        "project_wiki_uid": wiki.get_uid(),
        **(other_data or {}),
    }
