from typing import Any, Self
from ...core.db import Field
from ...core.types import SnowflakeID
from .bases import BaseGraphApprovalRequestModel
from .bases.BaseGraphApprovalRequestModel import graph_approval_request_api_field
from .GraphApprovalRequest import GraphApprovalOriginType, GraphApprovalRequest


class EditorGraphApprovalRequest(BaseGraphApprovalRequestModel, table=True):
    document_name: str = Field(nullable=False, index=True, api_field=graph_approval_request_api_field())

    @staticmethod
    def get_request_type() -> GraphApprovalOriginType:
        return GraphApprovalOriginType.Editor

    @classmethod
    def create_for_approval(
        cls, approval: GraphApprovalRequest, scope_table: str, scope_id: SnowflakeID | None, **context: Any
    ) -> Self:
        document_name = context.get("document_name")
        if not isinstance(document_name, str) or not document_name:
            raise ValueError("Editor graph approval request requires document name.")

        return cls(
            approval_request_id=approval.id,
            scope_table=scope_table,
            scope_id=scope_id,
            document_name=document_name,
        )
