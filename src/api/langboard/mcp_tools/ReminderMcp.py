"""Native reminder tools delegate scheduling and authority to the shared application."""

from typing import Annotated, Literal
from uuid import UUID
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule
from langboard_shared.security import RoleFinder
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, TypeAdapter
from ..card_workspace.application import reminders
from ..mcp_integration import McpRoleFilter, McpTool


Uid = Annotated[str, Field(min_length=1, max_length=32)]
ScheduleInput = Annotated[ReminderSchedule, BeforeValidator(reminders.schedule_adapter.validate_python)]
InstantInput = Annotated[AwareDatetime, BeforeValidator(TypeAdapter(AwareDatetime).validate_python)]


class CreateReminder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create"]
    schedule: ScheduleInput
    request_uid: Annotated[UUID, BeforeValidator(lambda value: UUID(value) if isinstance(value, str) else value)]
    shared: StrictBool = False
    external_delivery: StrictBool = False
    recipient_overrides: reminders.ReminderRecipientOverrides | None = None


class UpdateReminder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["update"]
    rule_uid: Uid
    schedule: ScheduleInput
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    enabled: StrictBool
    external_delivery: StrictBool | None = None
    recipient_overrides: reminders.ReminderRecipientOverrides | None = None


class ChangeOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["change_occurrence"]
    rule_uid: Uid
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_due_at: InstantInput
    moved_to: InstantInput | None = None


ReminderChange = Annotated[CreateReminder | UpdateReminder | ChangeOccurrence, Field(discriminator="action")]


@McpTool.add(
    "user",
    description="List currently readable personal and shared reminder rules on one card; follow next_cursor. Never schedules or marks notifications read.",
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def list_card_reminders(
    project_uid: Uid,
    card_uid: Uid,
    user: User,
    service: DomainService,
    cursor: Annotated[str, Field(max_length=32)] | None = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 20,
) -> dict:
    return reminders.list_card_reminder_rules(
        service, user, project_uid, card_uid, CollaborationChannel.Mcp, cursor=cursor, limit=limit
    )


@McpTool.add(
    "user",
    description="Create or update one reminder, or skip/move its observed next occurrence. Create requires an original request_uid; updates and occurrence changes require current revision. enabled=false pauses a rule. No notification delivery or card completion is implied.",
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def change_card_reminder(
    project_uid: Uid,
    card_uid: Uid,
    change: ReminderChange,
    user: User,
    service: DomainService,
) -> dict:
    arguments = change.model_dump(exclude={"action"}, mode="python")
    if isinstance(change, (CreateReminder, UpdateReminder)):
        arguments.update(schedule=change.schedule, recipient_overrides=change.recipient_overrides)
    if isinstance(change, CreateReminder):
        item, replayed = reminders.create_card_reminder_rule(
            service, user, project_uid, card_uid, CollaborationChannel.Mcp, **arguments
        )
        return {"item": item, "replayed": replayed}
    if isinstance(change, UpdateReminder):
        return {
            "item": reminders.update_card_reminder_rule(
                service, user, project_uid, card_uid, channel=CollaborationChannel.Mcp, **arguments
            )
        }
    return {
        "item": reminders.change_card_reminder_occurrence(
            service, user, project_uid, card_uid, channel=CollaborationChannel.Mcp, **arguments
        )
    }
