from typing import Any, Literal
from ..core.publisher import BaseSocketPublisher, SocketPublishModel
from ..core.routing import SocketTopic
from ..core.utils.decorators import staticclass
from ..domain.models import BotLog, BotSchedule, Project
from ..domain.models.bases import BaseBotLogModel, BaseBotScheduleModel, BaseBotScopeModel
from ..domain.models.BotLog import BotLogMessage
from ..helpers import BotHelper


@staticclass
class ProjectBotPublisher(BaseSocketPublisher):
    @staticmethod
    def scheduled(project: Project, schedule: tuple[BotSchedule, BaseBotScheduleModel]):
        target_table = BotHelper.get_target_table_by_bot_model("schedule", schedule[1].__class__)
        model = {
            "target_table": target_table,
            "schedule": {**schedule[0].api_response(), **schedule[1].api_response()},
        }
        topic_id = project.get_uid()
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:cron:scheduled",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def rescheduled(project: Project, schedule: BaseBotScheduleModel, updated: dict[str, Any]):
        target_table = BotHelper.get_target_table_by_bot_model("schedule", schedule.__class__)
        model = {
            "target_table": target_table,
            "uid": schedule.get_uid(),
            "updated": updated,
        }
        topic_id = project.get_uid()
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:cron:rescheduled",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def unscheduled(project: Project, schedule: BaseBotScheduleModel):
        target_table = BotHelper.get_target_table_by_bot_model("schedule", schedule.__class__)
        model = {"target_table": target_table, "uid": schedule.get_uid()}
        topic_id = project.get_uid()
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:cron:unscheduled",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def scope_created(project: Project, bot_scope: BaseBotScopeModel):
        scope_table = BotHelper.get_target_table_by_bot_model("scope", bot_scope.__class__)
        topic_id = project.get_uid()
        model = {"scope_table": scope_table, "bot_scope": bot_scope.api_response()}
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:scope:created",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def scope_conditions_updated(project: Project, bot_scope: BaseBotScopeModel):
        scope_table = BotHelper.get_target_table_by_bot_model("scope", bot_scope.__class__)
        model = {
            "scope_table": scope_table,
            "uid": bot_scope.get_uid(),
            "conditions": bot_scope.conditions,
            "default_scope_branch_uid": (
                bot_scope.default_scope_branch_id.to_short_code() if bot_scope.default_scope_branch_id else None
            ),
        }
        topic_id = project.get_uid()
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:scope:conditions:updated",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def scope_freeze_updated(project: Project, bot_scope: BaseBotScopeModel):
        scope_table = BotHelper.get_target_table_by_bot_model("scope", bot_scope.__class__)
        model = {
            "scope_table": scope_table,
            "uid": bot_scope.get_uid(),
            "is_frozen": bot_scope.is_frozen,
        }
        topic_id = project.get_uid()
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:scope:freeze:updated",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def scope_deleted(project: Project, bot_scope: BaseBotScopeModel):
        scope_table = BotHelper.get_target_table_by_bot_model("scope", bot_scope.__class__)
        topic_id = project.get_uid()
        model = {"scope_table": scope_table, "uid": bot_scope.get_uid()}
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:scope:deleted",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def log_created(project: Project, bot_log: tuple[BotLog, BaseBotLogModel]):
        topic_id = project.get_uid()
        model = {"log": {**bot_log[0].api_response(), **bot_log[1].api_response()}}
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:log:created",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)

    @staticmethod
    def log_stack_added(
        project: Project, bot_log: BotLog, stack: BotLogMessage, status: Literal["running", "stopped"] | None = None
    ):
        topic_id = project.get_uid()
        model = {
            "uid": bot_log.get_uid(),
            "log_type": bot_log.log_type.value,
            "updated_at": bot_log.updated_at,
            "stack": stack,
            "status": status,
        }
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:bot:log:stack:added",
            data_keys=list(model.keys()),
        )

        ProjectBotPublisher.put_dispather(model, publish_model)
