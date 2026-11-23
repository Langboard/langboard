"""Native checklist actions retain canonical validation and authorization."""

from typing import Annotated, Any, Literal
from langboard_shared.domain.models import Bot, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.security import RoleFinder
from pydantic import Field, StrictBool, model_validator
from ..mcp_integration.RoleFilter import McpRoleFilter
from ..mcp_integration.Tool import McpTool
from .CardActionsMcp import CardAction, Text, Uid


class CreateList(CardAction):
    action: Literal["create_list"]
    title: Text


class AddItem(CardAction):
    action: Literal["add_item"]
    checklist_uid: Uid
    title: Text


class UpdateList(CardAction):
    action: Literal["update_list"]
    checklist_uid: Uid
    title: Text | None = None
    is_checked: StrictBool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.title is None and self.is_checked is None:
            raise ValueError("Checklist title or checked state is required")
        return self


class DeleteList(CardAction):
    action: Literal["delete_list"]
    checklist_uid: Uid


class SetCompleted(CardAction):
    action: Literal["set_completed"]
    checkitem_uid: Uid
    is_checked: StrictBool


class UpdateItem(CardAction):
    action: Literal["update_item"]
    checkitem_uid: Uid
    title: Text | None = None
    deadline_at: str | None = None
    is_checked: StrictBool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.title is None and self.deadline_at is None and self.is_checked is None:
            raise ValueError("Checkitem title, deadline or checked state is required")
        return self


class DeleteItem(CardAction):
    action: Literal["delete_item"]
    checkitem_uid: Uid


class PromoteItem(CardAction):
    action: Literal["promote"]
    checkitem_uid: Uid
    project_column_uid: Uid


ChecklistChange = Annotated[
    CreateList | AddItem | UpdateList | DeleteList | SetCompleted | UpdateItem | DeleteItem | PromoteItem,
    Field(discriminator="action"),
]

CHECKLIST_ACTION_COMMANDS = {
    "create_list": "create_card_checklist",
    "add_item": "create_card_checkitem",
    "update_list": "update_card_checklist",
    "delete_list": "delete_card_checklist",
    "set_completed": "update_card_checkitem",
    "update_item": "update_card_checkitem",
    "delete_item": "delete_card_checkitem",
    "promote": "cardify_card_checkitem",
}


@McpTool.add(
    description="Change one native checklist or item with one action-specific change. Completion is explicit; promotion requires an active column. After ambiguous promotion, read the item's cardified_card link before retrying."
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
def change_card_checklist(
    project_uid: str, card_uid: str, change: ChecklistChange, user_or_bot: User | Bot, service: DomainService
) -> dict[str, Any]:
    """Dispatch to native owners; keep response projections at the MCP boundary."""
    from langboard_shared.core.db import DbSession
    from ..card_workspace.application.projections import public_card_summary, public_checkitem, public_checklist
    from .CardMcp import _checklist_mutation_result, _require_task_card

    action = change.action
    if action == "create_list":
        checklist = service.checklist.create(user_or_bot, project_uid, card_uid, change.title)
        if checklist is None:
            raise ValueError("Card not found in project")
        return {"checklist": public_checklist({**checklist.api_response(), "checkitems": []})}
    if action == "add_item":
        item = service.checkitem.create(user_or_bot, project_uid, card_uid, change.checklist_uid, change.title)
        if item is None:
            raise ValueError("Checklist not found in card")
        return {"checkitem": public_checkitem(item.api_response())}
    if action in {"update_list", "update_item", "set_completed"}:
        from fastmcp.exceptions import ValidationError
        from ..card_workspace.domain import CardUnavailableError

        with DbSession.atomic():
            _, card = _require_task_card(project_uid, card_uid, user_or_bot, service)
            if action == "update_list":
                updated = service.checklist.update_details(
                    user_or_bot, project_uid, card, change.checklist_uid,
                    title=change.title, is_checked=change.is_checked,
                )
            else:
                fields = change.model_dump(exclude={"action", "checkitem_uid"}, exclude_none=True)
                updated = service.checkitem.update_details(
                    user_or_bot, project_uid, card, change.checkitem_uid, **fields,
                )
            if not updated:
                raise ValidationError(
                    "CARD_UNAVAILABLE: Card not found in this project. Do not retry unchanged."
                ) from CardUnavailableError("Card not found in project")
            return _checklist_mutation_result(service, card_uid)
    if action == "delete_list":
        if not service.checklist.delete(user_or_bot, project_uid, card_uid, change.checklist_uid):
            raise ValueError("Checklist not found in card")
        return {"deleted": True}
    if action == "delete_item":
        if not service.checkitem.delete(user_or_bot, project_uid, card_uid, change.checkitem_uid):
            raise ValueError("Checkitem not found in card")
        return {"deleted": True}

    _, card = _require_task_card(project_uid, card_uid, user_or_bot, service)
    if not service.checkitem.cardify(user_or_bot, project_uid, card, change.checkitem_uid, change.project_column_uid):
        raise ValueError("Checkitem could not be cardified in the requested column")
    persisted = service.checkitem.get_by_id_like(change.checkitem_uid)
    created = service.card.get_by_id_like(persisted.cardified_id) if persisted is not None else None
    if created is None:
        raise RuntimeError("Cardified card could not be read back")
    return {
        "card": public_card_summary(created.board_api_response(0, [], [], [])),
        "source_checkitem_uid": change.checkitem_uid,
    }
