"""Private model results with fenced inference and attachment-generation references."""

from sqlalchemy import JSON, Index
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class DocumentEmbeddingCache(BaseDbModel, table=True):
    __table_args__ = (Index("ix_document_embedding_cache_key", "cache_key", unique=True),)
    cache_key: str = Field(nullable=False, max_length=64)
    vector: list[float] | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True))
    lease_token: str | None = Field(default=None, nullable=True, max_length=32)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["id"]


class DocumentEmbeddingCacheReference(BaseDbModel, table=True):
    __table_args__ = (
        Index("ix_document_embedding_cache_reference_use", "cache_id", "use_token", unique=True),
        Index("ix_document_embedding_cache_reference_token", "use_token"),
        Index("ix_document_embedding_cache_reference_expiry", "expires_at"),
    )
    cache_id: SnowflakeID = SnowflakeIDField(foreign_key=DocumentEmbeddingCache, nullable=False)
    use_token: str = Field(nullable=False, max_length=32)
    expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["id"]
