"""Durable terminal upload results; never coupled to mutable attachment lifetime."""

from typing import Any
from sqlalchemy import JSON, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Project import Project
from .User import User


class ProjectAttachmentUploadReceipt(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", "request_uid", name="uq_project_attachment_upload_request"),
    )

    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False)
    request_uid: str = Field(nullable=False, max_length=36)
    fingerprint: str = Field(nullable=False, max_length=64)
    result: dict[str, Any] = Field(nullable=False, sa_type=JSON)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["project_id", "user_id", "request_uid"]
