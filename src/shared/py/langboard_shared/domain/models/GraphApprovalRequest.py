import hashlib
import json
from copy import deepcopy
from datetime import timezone
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
    decision_case_id: SnowflakeID | None = SnowflakeIDField(
        foreign_key="decision_case.id", nullable=True, unique=True
    )
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
    execution_plan: dict[str, Any] | None = Field(
        default=None, nullable=True, sa_type=JSON(none_as_null=True), api_field=ApiField()
    )
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

    @staticmethod
    def freeze_execution_plan(value: dict[str, Any], interrupt_id: Any, run: Any) -> dict[str, Any] | None:
        if "execution_plan" not in value:
            return None
        plan = value["execution_plan"]
        if not isinstance(plan, dict):
            raise ValueError("Invalid execution plan")
        required = {
            "version",
            "plan_id",
            "tool_name",
            "action_type",
            "api_name",
            "arguments",
            "args_hash",
            "thread_id",
            "session_id",
        }
        native = {
            "run_id": run.client_task_id,
            "native_run_uid": run.get_uid(),
            "interrupt_id": interrupt_id,
            "generation": run.attempt,
        }
        if (
            set(plan) not in (required, required | set(native))
            or any(key in plan and plan[key] != expected for key, expected in native.items())
            or type(plan.get("version")) is not int
            or plan["version"] != 1
        ):
            raise ValueError("Unsupported execution plan contract")
        if any(
            not isinstance(plan.get(key), str) or not plan[key]
            for key in ("plan_id", "tool_name", "action_type", "args_hash", "thread_id", "session_id")
        ):
            raise ValueError("Missing execution plan identity")
        if (
            not isinstance(plan["arguments"], dict)
            or plan["arguments"] != value.get("request_payload")
            or plan["thread_id"] != run.graph_thread_id
            or plan["session_id"] != run.graph_session_id
            or not isinstance(interrupt_id, str)
            or not interrupt_id
            or run.attempt < 1
            or plan["tool_name"] != value.get("tool_name")
            or plan["action_type"] != value.get("action_type")
            or plan["api_name"] != value.get("api_name")
        ):
            raise ValueError("Execution plan does not match the interrupted run")
        digest = hashlib.sha256(
            json.dumps(
                plan["arguments"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
        if digest != plan["args_hash"]:
            raise ValueError("Execution plan arguments hash mismatch")
        return deepcopy({**plan, **native})

    def matches_execution_plan(self, run: Any, interrupt: dict[str, Any], *, completing: bool = False, claimant_id=None, decision=None, recording_result: bool = False) -> bool:
        if recording_result and not completing:
            return False
        value = interrupt.get("value", interrupt)
        if not isinstance(value, dict):
            return False
        # Attached multi-party decisions must never use the legacy resume path.
        if any(
            value.get(key) is not None or self.request_payload.get(key) is not None
            for key in ("decision_uid", "decision_case_uid", "multi_hitl", "approval_policy")
        ):
            return False
        if self.decision_case_id is not None:
            from ..services.DecisionHITL import DecisionHITL

            if not DecisionHITL.validate(self, run, completing=completing, claimant_id=claimant_id, decision=decision, recording_result=recording_result):
                return False
        if completing and self.execution_plan is not None:
            lease = run.lease_expires_at
            if lease is None:
                return False
            if lease.tzinfo is None:
                lease = lease.replace(tzinfo=timezone.utc)
            if lease <= SafeDateTime.now():
                return False
        if self.execution_plan is None:
            return "execution_plan" not in value
        try:
            from types import SimpleNamespace

            context = SimpleNamespace(
                graph_thread_id=run.graph_thread_id,
                graph_session_id=run.graph_session_id,
                attempt=run.attempt - (1 if completing else 0),
                client_task_id=run.client_task_id,
                get_uid=run.get_uid,
            )
            plan = self.freeze_execution_plan(value, interrupt.get("id"), context)
        except (ValueError, TypeError):
            return False
        return (
            plan == self.execution_plan
            and self.request_payload == plan["arguments"]
            and self.tool_name == plan["tool_name"]
            and self.action_type == plan["action_type"]
            and self.api_name == plan["api_name"]
            and self.thread_id == plan["thread_id"]
            and value.get("approval_uid") == self.get_uid()
            and (
                not completing
                or (
                    run.request_payload.get("resume_execution_plan") == plan
                    and run.request_payload.get("resume_approval_uid") == self.get_uid()
                )
            )
        )

    @property
    def origin_type(self) -> GraphApprovalRequestType:
        return self.request_type

    def notification_data(self) -> dict[str, Any]:
        return self.api_response()

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["request_type", "thread_id", "status"]
