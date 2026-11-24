"""Client-independent card actions reuse authorized native commands."""

from typing import Annotated, Any, Literal
from uuid import UUID
from fastmcp.exceptions import AuthorizationError, ToolError, ValidationError
from langboard_shared.core.exceptions.CardDeleteForbidden import CardDeleteForbidden
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Bot, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardVisibilityPolicy import (
    CardVisibilityConflict,
    CardVisibilityDenied,
    CardVisibilityInputRejected,
)
from langboard_shared.domain.services.DecisionGate import DecisionGateBlocked
from langboard_shared.security import RoleFinder
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, model_validator
from ..card_workspace.application import replace_card_description
from ..card_workspace.application.projections import bounded_items, public_attachment, public_label
from ..card_workspace.domain import CardBundleSection, DescriptionPatchConflict
from ..mcp_integration.ExecutionHistory import execution_scope, safe_scope
from ..mcp_integration.RoleFilter import McpRoleFilter
from ..mcp_integration.Tool import McpTool
from .CardMcp import (
    _adapter,
    _as_upload_request_uid,
    _card_delete_result,
    _card_move_result,
    _change_card_details,
    _get_card_in_project,
    _require_readable_card,
    _require_task_card,
    _upload_card_attachment,
)
from .RoleChecker import McpRoleChecker


Text = Annotated[str, Field(min_length=1, max_length=500)]
Uid = Annotated[str, Field(min_length=1, max_length=80)]
Order = Annotated[int, Field(strict=True, ge=0)]


class CardAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssignCard(CardAction):
    action: Literal["assign"]
    assign_user_uids: list[Uid]


class LabelCard(CardAction):
    action: Literal["label"]
    label_uids: list[Uid]


class TitleCard(CardAction):
    action: Literal["title"]
    title: Text


class PriorityCard(CardAction):
    action: Literal["priority"]
    priority: Literal["P0", "P1", "P2", "P3"]


class DeadlineCard(CardAction):
    action: Literal["deadline"]
    # The canonical command parses ISO time; an empty string explicitly clears it.
    deadline_at: str


class NextActionCard(CardAction):
    action: Literal["next_action"]
    # Empty text explicitly clears; the native service validates and normalizes.
    next_action: Annotated[str, Field(max_length=1000)]


class CompleteCard(CardAction):
    action: Literal["completion"]
    completed: Annotated[bool, Field(strict=True)]


class ReplaceCardDescription(CardAction):
    # Preserve reviewed body whitespace, including leading Markdown indentation.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    action: Literal["description_replace"]
    description: Annotated[str, Field(max_length=32000)]
    expected_revision: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]


class ChangeCardVisibility(CardAction):
    action: Literal["visibility"]
    visibility: Literal["PRIVATE", "INTERNAL", "SHARED"]
    expected_visibility: Literal["PRIVATE", "INTERNAL", "SHARED"]
    confirmed: Annotated[bool, Field(strict=True)] = False
    expected_updated_at: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def require_aware_review_time(self):
        if self.expected_updated_at is not None:
            TypeAdapter(AwareDatetime).validate_python(self.expected_updated_at)
        return self


class MoveCard(CardAction):
    action: Literal["move"]
    column_uid: Uid
    order: Order
    move_descendants: Annotated[bool, Field(strict=True)] | None = None
    confirmation_revision: str | None = None


class ArchiveCard(CardAction):
    action: Literal["archive"]


class DeleteCard(CardAction):
    action: Literal["delete"]


class UpdateCardAttachment(CardAction):
    action: Literal["attachment_update"]
    attachment_uid: Uid
    name: Text | None = None
    order: Order | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.order is None:
            raise ValueError("Attachment name or order is required")
        return self


class DeleteCardAttachment(CardAction):
    action: Literal["attachment_delete"]
    attachment_uid: Uid


class UploadCardAttachment(CardAction):
    action: Literal["attachment_upload"]
    filename: Text
    file_data_base64: str
    request_uid: Annotated[UUID, BeforeValidator(_as_upload_request_uid)]
    duplicate_action: Literal["add", "replace", "cancel"] | None = None
    confirmation_token: str | None = None
    replacement_uid: Uid | None = None


CardChange = Annotated[
    AssignCard
    | LabelCard
    | TitleCard
    | PriorityCard
    | DeadlineCard
    | NextActionCard
    | CompleteCard
    | ReplaceCardDescription
    | ChangeCardVisibility
    | MoveCard
    | ArchiveCard
    | DeleteCard
    | UpdateCardAttachment
    | DeleteCardAttachment
    | UploadCardAttachment,
    Field(discriminator="action"),
]

CARD_ACTION_COMMANDS = {
    "assign": "set_card_people_and_labels",
    "label": "set_card_people_and_labels",
    "title": "change_card_details",
    "deadline": "change_card_details",
    "priority": "change_card_details",
    "next_action": "change_card_details",
    "completion": "set_card_completed",
    "description_replace": "replace_card_description",
    "visibility": "change_card_visibility",
    "move": "change_card_order_or_move_column",
    "archive": "archive_card",
    "delete": "delete_card",
    "attachment_update": "update_card_attachment",
    "attachment_delete": "delete_card_attachment",
    "attachment_upload": "upload_card_attachment",
}


@McpTool.add(
    description="Change one card field, lifecycle state or attachment. Pass one action-specific change; body replacement requires its reviewed revision. Labels select existing labels only. Visibility requires expected_visibility. PRIVATE transitions require the current owner; PRIVATE or sharing confirmation requires explicit approval and exact timezone-aware expected_updated_at. Unconfirmed sharing returns the existing human confirmation handoff."
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def update_card(
    project_uid: str, card_uid: str, change: CardChange, user_or_bot: User | Bot, service: DomainService
) -> dict[str, Any]:
    """Call the native owner once with the current actor and one service lifetime."""
    from ..mcp_integration.Server import McpServer

    command = CARD_ACTION_COMMANDS[change.action]
    metadata = McpTool.get_tool(command)
    if metadata is None:
        raise RuntimeError("Native card command is unavailable")
    handler = metadata["handler"]
    if not McpServer._validate_auth(user_or_bot, command):
        raise AuthorizationError("Authentication required")
    arguments = {
        "project_uid": project_uid,
        "card_uid": card_uid,
        **change.model_dump(exclude={"action"}, exclude_none=True),
    }
    if isinstance(change, ChangeCardVisibility) and "confirmed" not in change.model_fields_set:
        arguments.pop("confirmed", None)
    scope = execution_scope.get()
    if scope is not None:
        resolved = safe_scope(user_or_bot, handler, arguments)
        if resolved:
            scope.update(resolved)
    if not McpRoleChecker(service).check_permission(handler, user_or_bot, arguments):
        raise AuthorizationError("Insufficient permissions")
    # Primitive metadata above supplies existing transport authority only. Business
    # mutations below enter their native owners without another MCP invocation.
    if isinstance(change, (AssignCard, LabelCard)):
        project, card = _require_task_card(project_uid, card_uid, user_or_bot, service)
        result = service.card.replace_people_and_labels(
            user_or_bot,
            project,
            card,
            **{key: value for key, value in arguments.items() if key not in {"project_uid", "card_uid"}},
        )
        if "labels" in result:
            result["labels"] = [public_label(label) for label in result["labels"]]
        return result
    if isinstance(change, (TitleCard, PriorityCard, DeadlineCard, NextActionCard)):
        return _change_card_details(user_or_bot=user_or_bot, service=service, **arguments)
    if isinstance(change, CompleteCard):
        _require_task_card(project_uid, card_uid, user_or_bot, service)
        if not service.card.set_card_completed(user_or_bot, project_uid, card_uid, change.completed):
            raise ValueError("Card completion checkbox is unavailable; read the card bundle")
        return {"completed": change.completed}
    if isinstance(change, ReplaceCardDescription):
        try:
            return replace_card_description(
                _adapter(user_or_bot, service), project_uid, card_uid, change.description, change.expected_revision
            )
        except DescriptionPatchConflict as exc:
            raise ValidationError(f"{exc}. No changes saved; read the description and review the replacement.") from exc
    if isinstance(change, ChangeCardVisibility):
        try:
            return service.card.review_visibility_change(
                user_or_bot,
                project_uid,
                card_uid,
                change.visibility,
                change.expected_visibility,
                change.confirmed,
                change.expected_updated_at,
            )
        except CardVisibilityInputRejected:
            raise
        except CardVisibilityConflict as exc:
            raise ToolError("Card visibility transition unavailable; read current state and verify access") from exc
        except CardVisibilityDenied as exc:
            raise AuthorizationError(
                "Card visibility transition unavailable; read current state and verify access"
            ) from exc
        except (PermissionError, ValueError):
            raise ToolError("Card visibility transition unavailable; read current state and verify access") from None
    if isinstance(change, MoveCard):
        try:
            result = service.card.change_order(
                user_or_bot,
                project_uid,
                card_uid,
                change.order,
                change.column_uid,
                move_descendants=change.move_descendants,
                confirmation_revision=change.confirmation_revision,
                channel=CollaborationChannel.Mcp,
            )
        except DecisionGateBlocked as exc:
            return exc.payload()
        return _card_move_result(result)
    if isinstance(change, ArchiveCard):
        project, card = _require_readable_card(project_uid, card_uid, user_or_bot, service)
        if not service.card.archive(user_or_bot, project, card):
            raise ValueError("Failed to archive")
        return {"message": "Removed from board" if card.is_linked_resource else "Archived"}
    if isinstance(change, DeleteCard):
        _require_readable_card(project_uid, card_uid, user_or_bot, service)
        try:
            result = service.card.delete(user_or_bot, project_uid, card_uid)
        except CardDeleteForbidden as exc:
            raise ValidationError(f"{exc.code}: {exc}") from exc
        return _card_delete_result(result)
    if isinstance(change, UploadCardAttachment):
        return _upload_card_attachment(user=user_or_bot, service=service, **arguments)
    params = _get_card_in_project(project_uid, card_uid)
    if not params:
        raise ValueError("Card not found in project")
    project, card = params
    attachment = service.card_attachment.get_by_id_like(change.attachment_uid)
    if attachment is None or attachment.card_id != card.id:
        raise ValueError("Attachment not found in card")
    if isinstance(change, DeleteCardAttachment):
        if not service.card_attachment.delete(user_or_bot, project, card, attachment):
            raise ValueError("Attachment not found in card")
        return {"deleted": True}
    if change.name is not None and not service.card_attachment.change_name(
        user_or_bot, project, card, attachment, change.name
    ):
        raise ValueError("Attachment not found in card")
    if change.order is not None and not service.card_attachment.change_order(
        project, card, attachment, change.order, user=user_or_bot
    ):
        raise ValueError("Attachment not found in card")
    return {
        "attachments": bounded_items(
            [public_attachment(item) for item in service.card_attachment.get_api_list_by_card(card_uid, limit=26)],
            CardBundleSection.Attachments,
            25,
        )
    }
