"""Native project-owned immutable file provenance."""

from typing import Any
from sqlalchemy import Index
from ...core.db import ApiField, Field, ModelColumnType, SnowflakeIDField, SoftDeleteModel
from ...core.storage import FileModel
from ...core.types import SnowflakeID
from .Project import Project
from .User import User


class ProjectAttachment(SoftDeleteModel, table=True):
    __table_args__ = (Index("ix_project_attachment_project_content_sha256", "project_id", "content_sha256"),)

    project_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Project, nullable=False, index=True, api_field=ApiField(name="project_uid")
    )
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    filename: str = Field(nullable=False, api_field=ApiField(name="name"))
    file: FileModel = Field(sa_type=ModelColumnType(FileModel), api_field=ApiField(name="url", field_base_model="path"))
    content_sha256: str = Field(nullable=False, max_length=64, exclude=True)
    folder_id: str = Field(default="inbox", nullable=False, max_length=16, api_field=ApiField())

    replaced_by_id: SnowflakeID | None = SnowflakeIDField(
        nullable=True, index=True, foreign_key="project_attachment.id"
    )

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["project_id", "user_id", "filename"]
