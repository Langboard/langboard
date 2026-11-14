"""Private conversion results and source-owned generation references."""

from sqlalchemy import JSON, TEXT, Index
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class DocumentExtractionCache(BaseDbModel, table=True):
    __table_args__ = (Index("ix_document_extraction_cache_key", "cache_key", unique=True),)
    cache_key: str = Field(nullable=False, max_length=64, exclude=True)
    markdown: str | None = Field(default=None, nullable=True, sa_type=TEXT, exclude=True)
    keywords: dict = Field(default_factory=dict, nullable=False, sa_type=JSON, exclude=True)
    document_json: dict | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True), exclude=True)
    lease_token: str | None = Field(default=None, nullable=True, max_length=32, exclude=True)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    def api_response(self):
        return {}

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["id"]


class DocumentExtractionCacheReference(BaseDbModel, table=True):
    __table_args__ = (
        Index("ix_document_extraction_cache_reference_use", "cache_id", "use_token", unique=True),
        Index("ix_document_extraction_cache_reference_token", "use_token"),
        Index("ix_document_extraction_cache_reference_expiry", "expires_at"),
        Index("ix_document_extraction_cache_reference_source", "source_type", "source_uid"),
    )
    cache_id: SnowflakeID = SnowflakeIDField(foreign_key=DocumentExtractionCache, nullable=False)
    use_token: str = Field(nullable=False, max_length=32, exclude=True)
    expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    source_type: str | None = Field(default=None, nullable=True, max_length=32, exclude=True)
    source_uid: str | None = Field(default=None, nullable=True, max_length=128, exclude=True)
    generation: str | None = Field(default=None, nullable=True, max_length=32, exclude=True)

    def api_response(self):
        return {}

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["id"]
