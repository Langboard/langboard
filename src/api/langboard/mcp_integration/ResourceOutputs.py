"""Typed attachment and wiki-link results preserve native privacy boundaries."""

from datetime import datetime
from typing import Literal
from pydantic import ConfigDict, Field, RootModel
from .Outputs import CommandOutput
from .WorkOutputs import ProjectionOutput


class PublicActorOutput(ProjectionOutput):
    uid: str | None = None
    type: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    username: str | None = None
    name: str | None = None
    bot_uname: str | None = None
    avatar: str | None = None
    uid_total_chars: int | None = None
    uid_truncated: bool | None = None
    type_total_chars: int | None = None
    type_truncated: bool | None = None
    firstname_total_chars: int | None = None
    firstname_truncated: bool | None = None
    lastname_total_chars: int | None = None
    lastname_truncated: bool | None = None
    username_total_chars: int | None = None
    username_truncated: bool | None = None
    name_total_chars: int | None = None
    name_truncated: bool | None = None
    bot_uname_total_chars: int | None = None
    bot_uname_truncated: bool | None = None
    avatar_total_chars: int | None = None
    avatar_truncated: bool | None = None


class AttachmentUploadOutput(CommandOutput):
    uid: str
    created_at: datetime | str | None
    updated_at: datetime | str | None
    card_uid: str
    name: str
    url: str
    order: int = Field(ge=0)
    request_uid: str
    replayed: bool = False


class AttachmentConflictOutput(CommandOutput):
    uid: str
    name: str
    same_name: bool
    same_content: bool
    can_replace: bool


class AttachmentConfirmationOutput(CommandOutput):
    confirmation_required: Literal[True]
    confirmation_token: str = Field(min_length=1)
    request_uid: str
    conflicts: list[AttachmentConflictOutput] = Field(max_length=100)


class AttachmentCancelledOutput(CommandOutput):
    cancelled: Literal[True]
    request_uid: str


class AttachmentUploadResponse(RootModel[AttachmentUploadOutput | AttachmentConfirmationOutput | AttachmentCancelledOutput]):
    """Native upload success, explicit duplicate decision, or cancellation."""

    # Every union branch is an object; declare that MCP's structured result is
    # already an object so native FastMCP preserves the existing flat wire shape.
    model_config = ConfigDict(json_schema_extra={"type": "object"})


class PublicAttachmentOutput(ProjectionOutput):
    uid: str
    image_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    name: str | None = None
    filename: str | None = None
    order: int | None = Field(default=None, ge=0)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    uid_total_chars: int | None = None
    uid_truncated: bool | None = None
    name_total_chars: int | None = None
    name_truncated: bool | None = None
    filename_total_chars: int | None = None
    filename_truncated: bool | None = None
    user: PublicActorOutput | None = None


class AttachmentPageOutput(ProjectionOutput):
    items: list[PublicAttachmentOutput]
    total_count: int = Field(ge=0)
    next_cursor: str | None
    limit: int = Field(ge=1)


class AttachmentUpdateOutput(CommandOutput):
    attachments: AttachmentPageOutput


class LinkedWikiOutput(CommandOutput):
    wiki_uid: str
    title: str


class WikiLinkMutationOutput(CommandOutput):
    wiki_uid: str
    linked: bool
    linked_wikis: list[LinkedWikiOutput]


class WikiFromCardOutput(WikiLinkMutationOutput):
    title: str
    card_archived: bool


RESOURCE_OUTPUTS = {
    "upload_card_attachment": AttachmentUploadResponse,
    "update_card_attachment": AttachmentUpdateOutput,
    "update_card_linked_wiki": WikiLinkMutationOutput,
    "create_wiki_from_card": WikiFromCardOutput,
}
