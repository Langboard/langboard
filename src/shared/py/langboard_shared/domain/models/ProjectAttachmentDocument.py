"""Private native project document state, fenced by immutable source and generation."""

from sqlalchemy import JSON, TEXT, CheckConstraint, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .ProjectAttachment import ProjectAttachment
from .User import User


class ProjectAttachmentDocument(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("attachment_id", name="uq_project_attachment_document_attachment"),
        CheckConstraint("status IN ('pending', 'processing', 'indexed', 'failed')", name="ck_project_document_status"),
    )
    attachment_id: SnowflakeID = SnowflakeIDField(foreign_key=ProjectAttachment, nullable=False)
    requested_by_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    source_revision: str = Field(nullable=False, max_length=64)
    generation: str = Field(nullable=False, max_length=32)
    status: str = Field(default="pending", nullable=False, max_length=16)
    vision_config: dict = Field(default_factory=dict, nullable=False, sa_type=JSON, exclude=True)
    embedding_config: dict | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True), exclude=True)
    markdown: str = Field(default="", nullable=False, sa_type=TEXT, exclude=True)
    keywords: dict = Field(default_factory=dict, nullable=False, sa_type=JSON, exclude=True)
    document_json: dict | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True), exclude=True)
    embedding: dict = Field(default_factory=dict, nullable=False, sa_type=JSON, exclude=True)
    text_source: dict | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True), exclude=True)
    vector_source: dict | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True), exclude=True)
    error: str | None = Field(default=None, nullable=True, max_length=128, exclude=True)

    def api_response(self):
        # Native state is internal; transports construct their authorized projections.
        return {"generation": self.generation, "status": self.status}

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["attachment_id", "generation", "status"]
