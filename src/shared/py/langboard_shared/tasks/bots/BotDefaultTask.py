from typing import Literal
from sqlalchemy.orm import defer
from ...core.broker import Broker
from ...core.db import DbSession, SqlBuilder
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import (
    Bot,
    Card,
    CardComment,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectWiki,
    ProjectWikiAssignedUser,
    User,
)
from ...helpers import InfraHelper
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


@BotTaskSchemaHelper.executor_schema(DomainEventType.BotCreated)
@Broker.wrap_async_task_decorator
async def bot_created(bot: Bot):
    await BotTaskHelper.run(bot, DomainEventType.BotCreated, BotTaskDataHelper.create_executor(bot))


def mention_event_args(user_or_bot, target_bot, mentioned_in, models):
    """Freeze queue references without retaining actor credentials or source bodies."""
    return (
        "user" if isinstance(user_or_bot, User) else "bot",
        user_or_bot.get_uid(),
        target_bot.get_uid(),
        mentioned_in,
        [(type(model).__tablename__, model.get_uid()) for model in models],
        next((model.editor_generation for model in models if isinstance(model, ProjectWiki)), None),
    )


@BotTaskSchemaHelper.executor_schema(
    DomainEventType.BotMentioned,
    {
        "mentioned_in": "Enum[card, comment, project_wiki]",
        "project_uid": "string",
        "project_column_uid?": "string",
        "card_uid?": "string",
        "comment_uid?": "string",
        "project_wiki_uid?": "string",
    },
)
@Broker.wrap_async_task_decorator
async def bot_mentioned(
    actor_kind: str,
    actor_uid: str,
    target_bot_uid: str,
    mentioned_in: Literal["card", "comment", "project_wiki"],
    model_refs: list[tuple[str, str]],
    wiki_generation: int | None,
):
    from ...domain.services.factory.BotService import BotService
    from ...domain.services.factory.ProjectWikiService import ProjectWikiService

    actor_model = {"user": User, "bot": Bot}.get(actor_kind)
    required = {
        "card": {"project", "card"},
        "comment": {"project", "card", "card_comment"},
        "project_wiki": {"project", "project_wiki"},
    }.get(mentioned_in)
    registry = {model.__tablename__: model for model in (Project, ProjectColumn, Card, CardComment, ProjectWiki)}
    if actor_model is None or required is None or not isinstance(model_refs, list):
        return
    try:
        refs = {table: InfraHelper.convert_id(uid) for table, uid in model_refs}
        actor_id, bot_id = InfraHelper.convert_id(actor_uid), InfraHelper.convert_id(target_bot_uid)
    except (TypeError, ValueError):
        return
    allowed = required | ({"project_column"} if mentioned_in != "project_wiki" else set())
    if len(refs) != len(model_refs) or not required <= refs.keys() or not refs.keys() <= allowed:
        return
    if mentioned_in == "project_wiki" and (type(wiki_generation) is not int or wiki_generation < 0):
        return
    if mentioned_in != "project_wiki" and wiki_generation is not None:
        return
    with DbSession.atomic() as db:
        actor = db.exec(SqlBuilder.select.table(actor_model).where(actor_model.id == actor_id)).first()
        target_bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == bot_id)).first()
        if actor is None or target_bot is None or actor.id == target_bot.id:
            return
        models = []
        bodies = {ProjectWiki: ProjectWiki.content, Card: Card.description, CardComment: CardComment.content}
        for table, record_id in refs.items():
            model = registry[table]
            query = SqlBuilder.select.table(model).where(model.id == record_id)
            if model in bodies:
                query = query.options(defer(bodies[model], raiseload=True))
            if model is ProjectWiki:
                query = query.with_for_update()
            current = db.exec(query).first()
            if current is None:
                return
            models.append(current)
        loaded = {type(model).__tablename__: model for model in models}
        project = loaded["project"]
        bot_permissions = BotService(None, None, None)
        if not bot_permissions.has_project_access(target_bot, project.get_uid()):
            return
        if isinstance(actor, User):
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id,
                    ProjectAssignedUser.user_id == actor.id,
                )
            ).first()
            if not actor.activated_at or (not actor.is_admin and project.owner_id != actor.id and member is None):
                return
        elif not bot_permissions.has_project_access(actor, project.get_uid()):
            return
        scope_model = project
        if mentioned_in == "project_wiki":
            wiki = loaded["project_wiki"]
            if wiki.project_id != project.id or wiki.editor_generation != wiki_generation:
                return
            assigned = db.exec(
                SqlBuilder.select.table(ProjectWikiAssignedUser).where(
                    ProjectWikiAssignedUser.project_wiki_id == wiki.id,
                )
            ).all()
            if not ProjectWikiService.can_view(actor, project, wiki, [item.user_id for item in assigned]):
                return
        else:
            card = loaded["card"]
            if card.project_id != project.id:
                return
            scope_model = card
            column = loaded.get("project_column")
            if column is not None and (column.project_id != project.id or card.project_column_id != column.id):
                return
            comment = loaded.get("card_comment")
            if comment is not None and comment.card_id != card.id:
                return
        data = {"mentioned_in": mentioned_in, **BotTaskDataHelper.create_executor(actor)}
        for model in models:
            data[f"{model.__tablename__}_uid"] = model.get_uid()
        # Use the canonical comment key for the existing current-source visibility fence.
        if "card_comment" in loaded:
            data["comment_uid"] = loaded["card_comment"].get_uid()
        if not BotTaskHelper.can_dispatch_card_scope(data, project, scope_model):
            return
    await BotTaskHelper.run(target_bot, DomainEventType.BotMentioned, data, project, scope_model)
