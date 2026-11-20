from datetime import timezone
from hashlib import sha256
from json import dumps
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, model_validator
from pydantic import Field as PydanticField
from sqlalchemy import JSON, Index
from ...core.db import ApiField, EditorContentModel, Field, ModelColumnType, SnowflakeIDField, SoftDeleteModel
from ...core.types import SnowflakeID
from .Bot import Bot
from .Card import Card
from .User import User


class CardCommentAnchorModel(BaseModel):
    """A content-based selector for a comment anchored to card description text.

    The exact quote and surrounding context follow the Web Annotation text quote
    selector model. Block text and paths are hints only, so document edits do not
    turn a positional offset into the comment's identity.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["TextQuoteSelector"] = "TextQuoteSelector"
    version: Literal[1] = 1
    exact: str = PydanticField(min_length=1, max_length=4096)
    prefix: str = PydanticField(default="", max_length=256)
    suffix: str = PydanticField(default="", max_length=256)
    start_block: str = PydanticField(default="", max_length=4096)
    end_block: str = PydanticField(default="", max_length=4096)
    start_path: list[Annotated[int, PydanticField(ge=0)]] = PydanticField(default_factory=list, max_length=16)
    end_path: list[Annotated[int, PydanticField(ge=0)]] = PydanticField(default_factory=list, max_length=16)


class CardCommentImageSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["card_attachment"]
    attachment_uid: str = PydanticField(min_length=1, max_length=64)
    revision: str = PydanticField(pattern=r"^[0-9a-f]{64}$")


class CardCommentImageAnchorModel(BaseModel):
    """Original-image pixel coordinates bound to one immutable attachment version."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["ImageRegionSelector"]
    version: StrictInt = PydanticField(ge=1, le=1)
    source: CardCommentImageSourceModel
    shape: Literal["point", "rectangle"]
    image_width: StrictInt = PydanticField(gt=0, le=100000)
    image_height: StrictInt = PydanticField(gt=0, le=100000)
    x: StrictInt | StrictFloat = PydanticField(ge=0, allow_inf_nan=False)
    y: StrictInt | StrictFloat = PydanticField(ge=0, allow_inf_nan=False)
    width: StrictInt | StrictFloat = PydanticField(ge=0, allow_inf_nan=False)
    height: StrictInt | StrictFloat = PydanticField(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_region(self):
        if self.x + self.width > self.image_width or self.y + self.height > self.image_height:
            raise ValueError("Image region exceeds original image dimensions")
        if self.shape == "point" and (self.width != 0 or self.height != 0):
            raise ValueError("Image points require zero width and height")
        if self.shape == "rectangle" and (self.width <= 0 or self.height <= 0):
            raise ValueError("Image rectangles require positive width and height")
        return self


# Keep legacy text selectors without an explicit type compatible.
CardCommentAnchor = CardCommentAnchorModel | CardCommentImageAnchorModel


class CardComment(SoftDeleteModel, table=True):
    __table_args__ = (Index("ix_card_comment_image_request_uid", "image_request_uid", unique=True),)

    # Retained on soft deletion and content edits as an immutable creation receipt.
    image_request_uid: str | None = Field(default=None, nullable=True, max_length=36, exclude=True)
    image_request_fingerprint: str | None = Field(default=None, nullable=True, max_length=64, exclude=True)

    card_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Card, nullable=False, index=True, api_field=ApiField(name="card_uid")
    )
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True)
    bot_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Bot, nullable=True)
    content: EditorContentModel = Field(
        default=EditorContentModel(), sa_type=ModelColumnType(EditorContentModel), api_field=ApiField()
    )
    anchor: dict[str, Any] | None = Field(default=None, nullable=True, sa_type=JSON, api_field=ApiField())

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema(
            {
                "is_edited": "bool",
                "content_revision": "string",
                **(schema or {}),
            }
        )

    def api_response(self) -> dict[str, Any]:
        return {
            **super().api_response(),
            "is_edited": self.created_at.timestamp() != self.updated_at.timestamp(),
            "content_revision": self.content_revision(),
        }

    def content_revision(self) -> str:
        """Opaque edit baseline; preserve sub-millisecond identity across clients."""
        updated = self.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        payload = [str(self.id), updated.astimezone(timezone.utc).isoformat(),
                   self.content.model_dump(mode="json"), self.anchor]
        return sha256(dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def notification_data(self) -> dict[str, Any]:
        return {
            "uid": self.get_uid(),
            "content": self.content.model_dump(),
        }

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["card_id", "user_id", "bot_id"]
