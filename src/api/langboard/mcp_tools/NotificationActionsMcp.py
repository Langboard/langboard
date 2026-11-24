"""Native notification scope selection preserves user-owned canonical commands."""

from typing import Annotated, Literal
from fastmcp.exceptions import AuthorizationError
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from pydantic import Field
from ..mcp_integration.ExecutionHistory import execution_scope, safe_scope
from ..mcp_integration.NativeConsent import native_owner_boundary
from ..mcp_integration.Outputs import NotificationReadOutput
from ..mcp_integration.Tool import McpTool
from .CardActionsMcp import CardAction, Uid
from .RoleChecker import McpRoleChecker


class ReadOneNotification(CardAction):
    scope: Literal["one"]
    notification_uid: Uid


class ReadAllNotifications(CardAction):
    scope: Literal["all"]


NotificationReadChange = Annotated[ReadOneNotification | ReadAllNotifications, Field(discriminator="scope")]
NOTIFICATION_READ_COMMANDS = {"one": "mark_notification_read", "all": "mark_all_notifications_read"}


@McpTool.add(
    "user",
    description="Mark the explicitly requested notification scope read. one requires notification_uid; all forbids it and marks only the current user's visible unread notifications. Queries never mark notifications read.",
)
def mark_notifications_read(
    change: NotificationReadChange, user: User, service: DomainService
) -> NotificationReadOutput:
    from ..mcp_integration.Server import McpServer

    command = NOTIFICATION_READ_COMMANDS[change.scope]
    metadata = McpTool.get_tool(command)
    if metadata is None:
        raise RuntimeError("Native notification command is unavailable")
    arguments = change.model_dump(exclude={"scope"})
    with native_owner_boundary(command, arguments):
        handler = metadata["handler"]
        if not McpServer._validate_auth(user, command):
            raise AuthorizationError("Authentication required")
        scope = execution_scope.get()
        if scope is not None:
            resolved = safe_scope(user, handler, arguments)
            if resolved:
                scope.update(resolved)
        if not McpRoleChecker(service).check_permission(handler, user, arguments):
            raise AuthorizationError("Insufficient permissions")
        if isinstance(change, ReadOneNotification):
            if not service.notification.read(user, change.notification_uid):
                raise ValueError("Notification not found")
        else:
            service.notification.read_all(user)
        return NotificationReadOutput(read=True)
