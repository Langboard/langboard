"""Durable installation intent; approval never grants board consent."""

import hashlib
import json
from datetime import timezone
from sqlalchemy import JSON, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


def _utc_timestamp(value):
    # SQLite drops offset metadata; PostgreSQL may return the same instant in
    # its session timezone. Convert aware values instead of relabeling them.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class AppInstallationRequest(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("requester_id", "request_key", name="uq_app_installation_request_identity"),)
    requester_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    request_key: str = Field(nullable=False)
    project_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    fingerprint: str = Field(nullable=False)
    artifact: dict = Field(sa_type=JSON, nullable=False)
    expected_app_revision: str | None = Field(default=None, nullable=True)
    decision: str = Field(default="pending", nullable=False, index=True)
    installation_status: str = Field(default="not_started", nullable=False)
    installation_error: str | None = Field(default=None, nullable=True)
    app: dict | None = Field(default=None, sa_type=JSON(none_as_null=True), nullable=True)
    reviewer_id: SnowflakeID | None = SnowflakeIDField(nullable=True)
    reason: str = Field(default="", nullable=False)
    generation: int = Field(default=1, nullable=False)

    def edit_revision(self):
        return hashlib.sha256(
            json.dumps([self.get_uid(), self.fingerprint, self.generation], separators=(",", ":")).encode()
        ).hexdigest()

    def response(self):
        artifact = self.artifact
        return {
            "uid": self.get_uid(),
            "requester_uid": self.requester_id.to_short_code(),
            "project_uid": self.project_id.to_short_code() if self.project_id else None,
            "source": artifact["provenance"]["kind"],
            "app_key": artifact["key"],
            "name": artifact["name"],
            "version": artifact["version"],
            "sha256": artifact["sha256"],
            "size": artifact["size"],
            "provenance": artifact["provenance"],
            "expected_app_revision": self.expected_app_revision,
            "decision": self.decision,
            "installation_status": self.installation_status,
            "installation_error": self.installation_error,
            "app": self.app,
            "reviewer_uid": self.reviewer_id.to_short_code() if self.reviewer_id else None,
            "reason": self.reason,
            "revision": self.edit_revision(),
            "created_at": _utc_timestamp(self.created_at),
            "updated_at": _utc_timestamp(self.updated_at),
        }

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["decision", "installation_status"]
