"""Canonical native comment mutations call the existing domain owner directly."""

from typing import Annotated, Literal
from uuid import UUID
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Bot, CardComment, ProjectRole, User
from langboard_shared.domain.models.CardComment import CardCommentAnchor, CardCommentImageAnchorModel
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.helpers import InfraHelper
from langboard_shared.security import RoleFinder
from pydantic import BeforeValidator, Field, model_serializer, model_validator
from ..card_workspace.application.projections import public_comment
from ..mcp_integration.Outputs import CommandOutput
from ..mcp_integration.RoleFilter import McpRoleFilter
from ..mcp_integration.Tool import McpTool
from ..mcp_integration.WorkOutputs import CommentOutput
from .CardActionsMcp import CardAction, Uid
from .CardMcp import _as_upload_request_uid


CardCommentReactionType = Literal[
    "check-mark",
    "confusing",
    "eyes",
    "heart",
    "laughing",
    "party-popper",
    "rocket",
    "thumbs-down",
    "thumbs-up",
]


class AddComment(CardAction):
    action: Literal["add"]
    content: Annotated[str, Field(min_length=1)]

    anchor: CardCommentAnchor | None = None
    request_uid: Annotated[UUID, BeforeValidator(_as_upload_request_uid)] | None = None

    @model_validator(mode="after")
    def require_image_request(self):
        if isinstance(self.anchor, CardCommentImageAnchorModel) and self.request_uid is None:
            raise ValueError("Image comments require a caller request UUID")
        return self


class EditComment(CardAction):
    action: Literal["edit"]
    content: Annotated[str, Field(min_length=1)]
    comment_uid: Uid


class DeleteComment(CardAction):
    action: Literal["delete"]
    comment_uid: Uid


class ReactComment(CardAction):
    action: Literal["react"]
    comment_uid: Uid
    reaction: CardCommentReactionType


CommentChange = Annotated[AddComment | EditComment | DeleteComment | ReactComment, Field(discriminator="action")]


class CommentActionOutput(CommandOutput):
    comment: CommentOutput | None = None
    deleted: Literal[True] | None = None
    is_reacted: bool | None = None
    replayed: bool | None = None

    @model_validator(mode="after")
    def require_one_result(self):
        if sum(value is not None for value in (self.comment, self.deleted, self.is_reacted)) != 1:
            raise ValueError("Expected exactly one comment action result")
        return self

    @model_serializer(mode="wrap")
    def serialize_result(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


@McpTool.add(
    description="Add, edit, delete or toggle a comment reaction. Edit/delete use the current native author or administrator policy; reactions do not approve workflows."
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def change_card_comment(
    project_uid: str,
    card_uid: str,
    change: CommentChange,
    user_or_bot: User | Bot,
    service: DomainService,
) -> CommentActionOutput:
    if change.action == "delete":
        if not service.card_comment.delete(user_or_bot, project_uid, card_uid, change.comment_uid):
            raise PermissionError("Comment not found or not owned by current actor")
        return CommentActionOutput(deleted=True)
    if change.action == "react":
        comment = service.card_comment.get_by_id_like(change.comment_uid)
        if comment is None:
            raise ValueError("Card comment not found")
        reacted = service.card_comment.toggle_reaction(user_or_bot, project_uid, card_uid, comment, change.reaction)
        if reacted is None:
            raise ValueError("Card comment not found")
        return CommentActionOutput(is_reacted=reacted)
    if change.action == "edit":
        comment = service.card_comment.update(
            user_or_bot, project_uid, card_uid, change.comment_uid, EditorContentModel(content=change.content)
        )
        if comment is None:
            raise PermissionError("Comment not found or not owned by current actor")
        return CommentActionOutput(comment=public_comment(comment.api_response()))
    comment = service.card_comment.create(
        user_or_bot,
        project_uid,
        card_uid,
        EditorContentModel(content=change.content),
        anchor=change.anchor,
        request_uid=str(change.request_uid) if change.request_uid is not None else None,
        channel=CollaborationChannel.Mcp,
    )
    if comment is None:
        raise ValueError("Card or image source unavailable")
    if isinstance(comment, dict):
        receipt = comment
        current = service.card.resolve_readable_card(project_uid, card_uid, user_or_bot, CollaborationChannel.Mcp)
        with DbSession.use(readonly=False) as db:
            comment = db.exec(
                SqlBuilder.select.table(CardComment).where(
                    CardComment.id == InfraHelper.convert_id(receipt["comment_uid"])
                )
            ).first()
        if current is None or comment is None or comment.card_id != current[1].id:
            raise ValueError("Created comment unavailable")
        return CommentActionOutput(comment=public_comment(comment.api_response()), replayed=receipt["replayed"])
    return CommentActionOutput(comment=public_comment(comment.api_response()))
