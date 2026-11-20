from hashlib import sha256
from json import dumps
from typing import Any
from sqlalchemy import TEXT, Index
from ...core.db import ApiField, Field, ModelColumnType, SnowflakeIDField, SoftDeleteModel
from ...core.storage import FileModel
from ...core.types import SnowflakeID
from .Card import Card
from .User import User


class CardAttachment(SoftDeleteModel, table=True):
    __table_args__ = (Index("ix_card_attachment_card_content_sha256", "card_id", "content_sha256"),)

    # Unknown for legacy attachments; never infer content identity from a filename.
    content_sha256: str | None = Field(default=None, nullable=True, max_length=64, exclude=True)
    # Historical rows point directly to the stable live attachment, never another alias.
    replaced_by_id: SnowflakeID | None = SnowflakeIDField(nullable=True, index=True)
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    card_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Card, nullable=False, index=True, api_field=ApiField(name="card_uid")
    )
    filename: str = Field(nullable=False, api_field=ApiField(name="name"))
    file: FileModel = Field(sa_type=ModelColumnType(FileModel), api_field=ApiField(name="url", field_base_model="path"))
    # Derived search projection; never expose whole documents in attachment responses.
    document_text: str = Field(
        default="", nullable=False, exclude=True, sa_type=TEXT, sa_column_kwargs={"server_default": ""}
    )
    order: int = Field(default=0, api_field=ApiField())

    @property
    def image_revision(self) -> str:
        # Object keys are immutable; display name, signed URLs and hash backfills
        # must not change the identity of the same stored image.
        identity = [self.get_uid(), self.file.storage_type, self.file.storage_name, self.file.filename]
        return sha256(dumps(identity, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema({"image_revision": "str", **(schema or {})})

    def api_response(self) -> dict[str, Any]:
        return {**super().api_response(), "image_revision": self.image_revision}

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["user_id", "card_id", "filename", "file"]
