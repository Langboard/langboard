from typing import Literal
from ...ai import BotDefaultTrigger
from ...core.broker import Broker
from ...core.db import BaseDbModel
from ...core.types.BotRelatedTypes import AVAILABLE_BOT_TARGET_TABLES
from ...domain.models import Bot, Project, User
from ...helpers import ModelHelper
from .utils import BotTaskDataHelper, BotTaskHelper, BotTaskSchemaHelper


@BotTaskSchemaHelper.executor_schema(BotDefaultTrigger.BotCreated)
@Broker.wrap_async_task_decorator
async def bot_created(bot: Bot):
    await BotTaskHelper.run(bot, BotDefaultTrigger.BotCreated, BotTaskDataHelper.create_executor(bot))


@BotTaskSchemaHelper.executor_schema(
    BotDefaultTrigger.BotMentioned,
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
    user_or_bot: User | Bot,
    target_bot: Bot,
    mentioned_in: Literal["card", "comment", "project_wiki"],
    dumped_models: list[tuple[str, dict]],
):
    models: list[BaseDbModel] = []
    for dumped_model in dumped_models:
        table_model, model_data = dumped_model
        table = ModelHelper.get_model_by_table_name(table_model)
        if not table:
            continue
        try:
            model = table.model_validate(model_data)
            models.append(model)
        except Exception:
            continue

    data = {"mentioned_in": mentioned_in, **BotTaskDataHelper.create_executor(user_or_bot)}
    project = None
    scope_model: BaseDbModel | None = None
    for model in models:
        data[f"{model.__tablename__}_uid"] = model.get_uid()
        if isinstance(model, Project):
            project = model
        if model.__tablename__ in AVAILABLE_BOT_TARGET_TABLES:
            scope_model = model

    if not project:
        return

    await BotTaskHelper.run(target_bot, BotDefaultTrigger.BotMentioned, data, project, scope_model)
