from abc import abstractmethod
from typing import Any, Self
from ....core.db import ApiField, BaseDbModel, Field, SnowflakeIDField
from ....core.types import SnowflakeID
from ..GraphApprovalRequest import GraphApprovalOriginType, GraphApprovalRequest


def graph_approval_request_api_field(name: str | None = None) -> ApiField:
    return ApiField(name=name, by_conditions={"is_graph_approval_request": ("api", True)})


class BaseGraphApprovalRequestModel(BaseDbModel):
    approval_request_id: SnowflakeID = SnowflakeIDField(
        foreign_key=GraphApprovalRequest,
        nullable=False,
        index=True,
        api_field=graph_approval_request_api_field("approval_request_uid"),
    )
    scope_table: str = Field(nullable=False, api_field=graph_approval_request_api_field())
    scope_id: SnowflakeID | None = SnowflakeIDField(
        nullable=True, index=True, api_field=graph_approval_request_api_field("scope_uid")
    )

    @staticmethod
    @abstractmethod
    def get_request_type() -> GraphApprovalOriginType: ...

    @classmethod
    def create_for_approval(
        cls, approval: GraphApprovalRequest, scope_table: str, scope_id: SnowflakeID | None, **_: Any
    ) -> Self:
        return cls(
            approval_request_id=approval.id,
            scope_table=scope_table,
            scope_id=scope_id,
        )

    def notification_data(self) -> dict[str, Any]:
        return self.api_response()

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["approval_request_id", "scope_table", "scope_id"]
