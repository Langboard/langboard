from datetime import datetime
from typing import Any, Literal
from langboard_shared.domain.models.CardComment import CardCommentAnchor
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from typing_extensions import Required, TypedDict


class BoundedItemsDto(BaseModel):
    """One bounded collection projection with an opaque continuation."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]
    total_count: int
    next_cursor: str | None
    limit: int


class CommentDto(TypedDict, total=False):
    """Validated selector evidence, preserving native dictionary access and omissions."""

    __pydantic_config__ = ConfigDict(extra="forbid")
    uid: Required[str]
    kind: Literal["comment"]
    evidence_ref: str
    created_at: datetime | str | None
    updated_at: datetime | str | None
    content: Required[str]
    content_format: Required[Literal["text", "json"]]
    content_total_chars: Required[int]
    content_truncated: Required[bool]
    anchor: CardCommentAnchor | None
    user: dict[str, Any]
    bot: dict[str, Any]
    reactions: dict[str, list[str]]
    reaction_counts: dict[str, int]


class ActivityCommentDto(TypedDict):
    """A native movement entry in the same bounded comment timeline."""

    __pydantic_config__ = ConfigDict(extra="forbid")
    kind: Literal["activity"]
    uid: str
    evidence_ref: str
    created_at: datetime | str | None
    actor: dict[str, Any] | None
    from_column: dict[str, Any]
    to_column: dict[str, Any]


class CompactionCommentDto(TypedDict):
    """Public summary projection; originals remain behind evidence references."""

    __pydantic_config__ = ConfigDict(extra="forbid")
    kind: Literal["compaction"]
    uid: str
    evidence_ref: str
    created_at: datetime | str | None
    summary: dict[str, Any]


class CommentItemsDto(BoundedItemsDto):
    items: list[CommentDto | ActivityCommentDto | CompactionCommentDto]


class PeopleDto(BoundedItemsDto):
    """Assignees plus timer workers; workers never imply assignment."""

    active_workers: list[dict[str, Any]] = Field(default_factory=list)


class BoundedTextDto(BaseModel):
    """One bounded text fragment with an opaque continuation."""

    model_config = ConfigDict(extra="forbid")

    content: str
    format: str
    total_chars: int
    revision: str
    next_cursor: str | None


class ClassificationDto(BaseModel):
    """Bounded labels and relationships for a card."""

    model_config = ConfigDict(extra="forbid")

    labels: BoundedItemsDto
    relationships: BoundedItemsDto


class AutomationDto(BaseModel):
    """Bounded native bot scope and schedule projections."""

    model_config = ConfigDict(extra="forbid")

    bot_scopes: BoundedItemsDto
    bot_schedules: BoundedItemsDto


class CardBundleDto(BaseModel):
    """Agent-facing native card aggregate."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    core: dict[str, Any]
    workflow: dict[str, Any]
    work_state: dict[str, Any] | None = None
    people: PeopleDto | None = None
    classification: ClassificationDto | None = None
    checklists: BoundedItemsDto | None = None
    comments: CommentItemsDto | None = None
    attachments: BoundedItemsDto | None = None
    metadata: BoundedItemsDto | None = None
    automation: AutomationDto | None = None
    content_blocks: BoundedItemsDto | None = None

    @field_validator("comments", mode="before")
    @classmethod
    def normalize_comment_page(cls, value):
        return value.model_dump() if isinstance(value, BoundedItemsDto) else value

    @model_serializer(mode="wrap")
    def serialize_selected_sections(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Omit sections that callers did not explicitly request."""

        return {key: value for key, value in handler(self).items() if value is not None}


class CardBundleContinuationDto(BaseModel):
    """Continuation-only response for one selected card section."""

    model_config = ConfigDict(extra="forbid")

    section: str
    page: BoundedItemsDto | None = None
    text: BoundedTextDto | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_comment_page(cls, value):
        if isinstance(value, dict) and value.get("section") == "comments" and value.get("page") is not None:
            page = value["page"]
            page = page.model_dump() if isinstance(page, BaseModel) else page
            return {**value, "page": CommentItemsDto.model_validate(page)}
        return value

    @model_validator(mode="after")
    def validate_payload(self) -> "CardBundleContinuationDto":
        """Require exactly one continuation payload shape."""

        if (self.page is None) == (self.text is None):
            raise ValueError("A continuation must contain exactly one page or text payload")
        return self


class CardBundleResponse(BaseModel):
    """Structured MCP output for one card bundle."""

    model_config = ConfigDict(extra="forbid")

    card_uid: str
    card: CardBundleDto | None = None
    continuation: CardBundleContinuationDto | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "CardBundleResponse":
        """Return either the initial bundle or one continuation, never both."""

        if (self.card is None) == (self.continuation is None):
            raise ValueError("Response must contain exactly one card bundle or continuation")
        return self


class AuthenticatedActorDto(BaseModel):
    """Minimal identity from the server-authenticated principal, never a client hint."""

    model_config = ConfigDict(extra="forbid")

    uid: str
    type: Literal["user", "bot"]


class ProjectIdentityResponse(BaseModel):
    """Stable project identity plus its bounded active workflow columns."""

    model_config = ConfigDict(extra="forbid")

    uid: str
    title: str
    project_type: str
    url: str
    columns: BoundedItemsDto
    authenticated_actor: AuthenticatedActorDto | None = None


class ProjectCardListResponse(BaseModel):
    """Bounded project card list."""

    model_config = ConfigDict(extra="forbid")

    project_uid: str
    cards: BoundedItemsDto
    workflow_stages: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ProjectCardIndexResponse(ProjectCardListResponse):
    """Workflow-aware index for modern MCP and native query consumers."""

    columns: dict[str, dict[str, Any]] = Field(default_factory=dict)
    format: Literal["tree"] | None = None
    relationships: list[dict[str, Any]] | None = None
    relationship_scope: Literal["current_page"] | None = None
    relationships_truncated: bool | None = None

    @model_serializer(mode="wrap")
    def serialize_format(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}
