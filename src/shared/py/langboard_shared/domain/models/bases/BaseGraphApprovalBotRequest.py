from typing import Any, Self
from ....core.db import SnowflakeIDField
from ....core.types import SnowflakeID
from ..Bot import Bot
from ..BotLog import BotLog
from ..GraphApprovalRequest import GraphApprovalRequest
from ..InternalBot import InternalBot
from .BaseGraphApprovalRequestModel import BaseGraphApprovalRequestModel, graph_approval_request_api_field


class BaseGraphApprovalBotRequest(BaseGraphApprovalRequestModel):
    bot_id: SnowflakeID | None = SnowflakeIDField(
        foreign_key=Bot, nullable=True, index=True, api_field=graph_approval_request_api_field("bot_uid")
    )
    internal_bot_id: SnowflakeID | None = SnowflakeIDField(
        foreign_key=InternalBot,
        nullable=True,
        index=True,
        api_field=graph_approval_request_api_field("internal_bot_uid"),
    )
    bot_log_id: SnowflakeID | None = SnowflakeIDField(
        foreign_key=BotLog, nullable=True, index=True, api_field=graph_approval_request_api_field("bot_log_uid")
    )

    @classmethod
    def create_for_approval(
        cls, approval: GraphApprovalRequest, scope_table: str, scope_id: SnowflakeID | None, **context: Any
    ) -> Self:
        bot = context.get("bot")
        internal_bot = context.get("internal_bot")
        bot_log = context.get("bot_log")
        return cls(
            approval_request_id=approval.id,
            scope_table=scope_table,
            scope_id=scope_id,
            bot_id=bot.id if isinstance(bot, Bot) else None,
            internal_bot_id=internal_bot.id if isinstance(internal_bot, InternalBot) else None,
            bot_log_id=bot_log.id if isinstance(bot_log, BotLog) else None,
        )
