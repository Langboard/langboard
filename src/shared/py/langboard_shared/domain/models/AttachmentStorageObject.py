"""Private physical bytes and independent native attachment locators.

These rows confer no read authority. Native attachment provenance and current
card, board or wiki authorization remain mandatory before resolving bytes.
"""

from sqlalchemy import BigInteger, Index
from ...core.db import BaseDbModel, Field
from ...core.db.ColumnTypes import SnowflakeIDType
from ...core.types import SnowflakeID


class AttachmentStorageObject(BaseDbModel, table=True):
    __table_args__ = (Index("uq_attachment_storage_content", "storage_type", "content_sha256", "size_bytes", unique=True),)

    storage_type: str = Field(nullable=False, max_length=32, exclude=True)
    content_sha256: str = Field(nullable=False, max_length=64, exclude=True)
    size_bytes: int = Field(nullable=False, sa_type=BigInteger, exclude=True)
    allocation_token: str = Field(nullable=False, max_length=32, exclude=True)
    ready: bool = Field(default=False, nullable=False, exclude=True)

    def api_response(self):
        return {}

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["id"]


class AttachmentStorageReference(BaseDbModel, table=True):
    __table_args__ = (
        Index("uq_attachment_storage_locator", "storage_type", "storage_name", "filename", unique=True),
        Index("ix_attachment_storage_reference_object", "object_id"),
    )

    object_id: SnowflakeID = Field(
        nullable=False, foreign_key=AttachmentStorageObject.expr("id"), sa_type=SnowflakeIDType, exclude=True
    )
    storage_type: str = Field(nullable=False, max_length=32, exclude=True)
    storage_name: str = Field(nullable=False, max_length=64, exclude=True)
    filename: str = Field(nullable=False, max_length=255, exclude=True)
    original_filename: str = Field(nullable=False, max_length=255, exclude=True)
    released: bool = Field(default=False, nullable=False, exclude=True)

    def api_response(self):
        return {}

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["id"]
