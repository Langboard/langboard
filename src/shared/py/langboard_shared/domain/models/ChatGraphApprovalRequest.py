from typing import Any, Self
from ...core.db import SnowflakeIDField
from ...core.types import SnowflakeID
from .bases import BaseGraphApprovalRequestModel
from .bases.BaseGraphApprovalRequestModel import graph_approval_request_api_field
from .ChatHistory import ChatHistory
from .ChatSession import ChatSession
from .GraphApprovalRequest import GraphApprovalOriginType, GraphApprovalRequest


class ChatGraphApprovalRequest(BaseGraphApprovalRequestModel, table=True):
    chat_session_id: SnowflakeID = SnowflakeIDField(
        foreign_key=ChatSession,
        nullable=False,
        index=True,
        api_field=graph_approval_request_api_field("chat_session_uid"),
    )
    chat_history_id: SnowflakeID = SnowflakeIDField(
        foreign_key=ChatHistory,
        nullable=False,
        index=True,
        api_field=graph_approval_request_api_field("chat_history_uid"),
    )

    @staticmethod
    def get_request_type() -> GraphApprovalOriginType:
        return GraphApprovalOriginType.Chat

    @classmethod
    def create_for_approval(
        cls, approval: GraphApprovalRequest, scope_table: str, scope_id: SnowflakeID | None, **context: Any
    ) -> Self:
        chat_session = context.get("chat_session")
        chat_history = context.get("chat_history")
        if not isinstance(chat_session, ChatSession) or not isinstance(chat_history, ChatHistory):
            raise ValueError("Chat graph approval request requires chat session and chat history.")

        return cls(
            approval_request_id=approval.id,
            scope_table=scope_table,
            scope_id=scope_id,
            chat_session_id=chat_session.id,
            chat_history_id=chat_history.id,
        )
