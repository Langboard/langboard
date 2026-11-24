"""Current-user notification and governed project-search MCP tools."""

from datetime import timedelta
from typing import Annotated, Literal
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from pydantic import Field
from ..card_workspace.application.queries.search import _parse_time_bound, search_card_context
from ..mcp_integration import McpTool


@McpTool.add(
    "user", description="Read the current user's employee status from explicit SCIM policy; unknown if unconfigured."
)
def get_employee_status(user: User, service: DomainService) -> dict[str, bool | str | None]:
    """Classification never grants authorization."""
    return {
        "policy_status": service.scim_provisioning.employee_policy_status(),
        "is_employee": service.scim_provisioning.is_employee(user),
    }


@McpTool.add("user", description="List a bounded admin-only employee directory without email addresses.")
def list_employees(user: User, service: DomainService, page: int = 1, limit: int = 50) -> dict:
    """A login or employee classification alone never grants directory access."""
    if not user.is_admin:
        raise PermissionError("Employee directory requires administrator access")
    return service.scim_provisioning.list_employees(page=page, limit=limit)


NotificationTimeRange = Literal["3d", "7d", "1m", "all"]
MyWorkPurpose = Literal["assigned", "mentioned", "due_soon", "overdue", "created"]


@McpTool.add(
    "user",
    description="List unread notifications for the current user without marking them as read.",
)
def get_unread_notifications(
    user: User,
    service: DomainService,
    time_range: NotificationTimeRange = "all",
    page: int = 1,
    limit: int = 20,
) -> dict:
    """Return unread notifications that remain unread after this query."""

    if page < 1 or not 1 <= limit <= 50:
        raise ValueError("page must be positive and limit must be between 1 and 50")
    notifications, _, _ = service.notification.get_api_list(
        user,
        time_range,
        page,
        limit,
        unread_only=True,
        authorized_projects_only=True,
        channel=CollaborationChannel.Mcp,
    )
    return {
        "notifications": notifications,
        "returned_count": len(notifications),
    }


@McpTool.add(
    "user",
    description="Mark one notification owned by the current user as read after explicit user approval.",
)
def mark_notification_read(notification_uid: str, user: User, service: DomainService) -> dict[str, bool]:
    """Mark one owned notification as read."""

    if not service.notification.read(user, notification_uid):
        raise ValueError("Notification not found")
    return {"read": True}


@McpTool.add(
    "user",
    description="Mark every unread notification for the current user as read after explicit user approval.",
)
def mark_all_notifications_read(user: User, service: DomainService) -> dict[str, bool]:
    """Mark every notification owned by the current user as read."""

    service.notification.read_all(user)
    return {"read": True}


@McpTool.add(
    description=(
        "Search a bounded page inside one readable project, "
        "or pass project_uid=null for all currently readable boards. "
        "Filter groups combine with AND, values within each group with OR. "
        "Follow next_cursor with identical query filters."
    )
)
def search_project_cards(
    project_uid: str | None,
    query: str,
    service: DomainService,
    date_field: Literal["created_at", "updated_at"] = "updated_at",
    since: str | None = None,
    until: str | None = None,
    include_closed: bool = False,
    workflow_stages: list[str] | None = None,
    visibility: list[str] | None = None,
    created_by: list[str] | None = None,
    assigned_to: list[str] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Field(ge=1, le=25, strict=True)] = 20,
    *,
    user: User,
) -> dict:
    """Search bounded card context through the native project-scoped query."""

    return search_card_context(
        project_uid,
        query,
        service,
        date_field=date_field,
        since=since,
        until=until,
        include_closed=include_closed,
        workflow_stages=workflow_stages,
        visibility=visibility,
        created_by=created_by,
        assigned_to=assigned_to,
        cursor=cursor,
        limit=limit,
        user=user,
    )


@McpTool.add(
    "user",
    description=(
        "List the current user's active work across readable projects. Results are deduplicated and "
        "independent of notification read state."
    ),
)
def get_my_work_cards(
    user: User,
    service: DomainService,
    purposes: list[MyWorkPurpose] | None = None,
    project_uid: str | None = None,
    date_field: Literal["created_at", "updated_at"] = "updated_at",
    since: str | None = None,
    until: str | None = None,
    due_within_days: Annotated[int, Field(ge=1, le=30)] = 7,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict:
    """Return a compact My Work queue filtered by current-user relationships."""

    selected = set(purposes or ["assigned", "mentioned", "due_soon", "overdue", "created"])
    if not selected <= {"assigned", "mentioned", "due_soon", "overdue", "created"}:
        raise ValueError("purposes contain an unsupported value")
    if not 1 <= due_within_days <= 30 or not 1 <= limit <= 50:
        raise ValueError("due_within_days must be 1-30 and limit must be 1-50")

    lower, upper = _parse_time_bound(since), _parse_time_bound(until)
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("since must be earlier than until")

    contexts = service.card.resolve_work_visibility_contexts(user, CollaborationChannel.Mcp)
    accessible_projects = [{"uid": SnowflakeID(project_id).to_short_code()} for project_id in contexts]
    if project_uid is not None:
        accessible_projects = [project for project in accessible_projects if project["uid"] == project_uid]
        if not accessible_projects:
            raise ValueError("Project not found or not readable")

    if not accessible_projects:
        return {"cards": [], "returned_count": 0}

    mention_sensitive = {"mentioned", "due_soon", "overdue"}
    mentioned_card_ids = service.notification.get_mentioned_card_ids(user) if selected & mention_sensitive else []
    due_before = SafeDateTime.now() + timedelta(days=due_within_days)
    cards = service.card.get_my_work_cards(
        user,
        accessible_projects,
        selected,
        mentioned_card_ids,
        due_before,
        date_field,
        lower,
        upper,
        limit,
        channel=CollaborationChannel.Mcp,
        contexts=contexts,
    )
    return {
        "cards": cards,
        "returned_count": len(cards),
    }


@McpTool.add(
    "user", description="List current user's assigned cards across readable boards using one bounded card query."
)
def list_my_work(
    user: User,
    service: DomainService,
    project_uid: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 20,
) -> dict:
    return service.card.list_assigned_work(user, project_uid, cursor, limit, channel=CollaborationChannel.Mcp)
