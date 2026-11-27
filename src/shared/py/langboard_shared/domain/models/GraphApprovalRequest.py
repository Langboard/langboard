from enum import Enum
from typing import Any, TypeAlias
from sqlalchemy import JSON
from ...core.db import ApiField, BaseDbModel, DateTimeField, EnumLikeType, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class GraphApprovalRequestType(Enum):
    Chat = "chat"
    Editor = "editor"
    Trigger = "trigger"
    Schedule = "schedule"
    ManualScopeRun = "manual_scope_run"


GraphApprovalOriginType: TypeAlias = GraphApprovalRequestType


class GraphApprovalStatus(Enum):
    Pending = "pending"
    Approved = "approved"
    Rejected = "rejected"
    Expired = "expired"
    Cancelled = "cancelled"
    Resolved = "resolved"


class GraphApprovalRequest(BaseDbModel, table=True):
    requested_by_user_id: SnowflakeID | None = SnowflakeIDField(
        nullable=True, index=True, api_field=ApiField(name="requested_by_user_uid")
    )
    resolved_by_user_id: SnowflakeID | None = SnowflakeIDField(
        nullable=True, index=True, api_field=ApiField(name="resolved_by_user_uid")
    )
    thread_id: str = Field(nullable=False, index=True, api_field=ApiField())
    run_id: str = Field(default="", nullable=False, index=True, api_field=ApiField())
    request_type: GraphApprovalRequestType = Field(
        nullable=False,
        index=True,
        sa_type=EnumLikeType(GraphApprovalRequestType),
        api_field=ApiField(name="origin_type"),
    )
    action_type: str = Field(default="api_call", nullable=False, api_field=ApiField())
    permission: str = Field(default="", nullable=False, index=True, api_field=ApiField())
    tool_name: str | None = Field(default=None, nullable=True, api_field=ApiField())
    api_name: str | None = Field(default=None, nullable=True, index=True, api_field=ApiField())
    request_payload: dict[str, Any] = Field(default_factory=dict, nullable=False, sa_type=JSON)
    preview_payload: dict[str, Any] = Field(default_factory=dict, nullable=False, sa_type=JSON, api_field=ApiField())
    status: GraphApprovalStatus = Field(
        default=GraphApprovalStatus.Pending,
        nullable=False,
        index=True,
        sa_type=EnumLikeType(GraphApprovalStatus),
        api_field=ApiField(),
    )
    resolved_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())
    expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())
    rejection_reason: str | None = Field(default=None, nullable=True, api_field=ApiField())

    @property
    def origin_type(self) -> GraphApprovalRequestType:
        return self.request_type

    def notification_data(self) -> dict[str, Any]:
        return self.api_response()

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["request_type", "thread_id", "status"]
