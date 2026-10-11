import hashlib
import json
from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Project import Project


class BoardAppBinding(BaseDbModel, table=True):
    """Board-owned App settings, independent of the discovery catalog."""

    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    app_key: str = Field(nullable=False)
    state: str = Field(default="disabled", nullable=False)
    workflow_mapping: dict[str, str] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    granted_capabilities: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)
    stage_transitions_enabled: bool = Field(default=False, nullable=False)
    archive_sync_types: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)
    archive_sync_revision: int = Field(default=1, nullable=False)

    def edit_revision(self) -> str:
        payload = json.dumps(
            {
                "id": int(self.id),
                "state": self.state,
                "workflow_mapping": self.workflow_mapping,
                "granted_capabilities": self.granted_capabilities,
                "stage_transitions_enabled": self.stage_transitions_enabled,
                "archive_sync_types": self.archive_sync_types,
                "archive_sync_revision": self.archive_sync_revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["project_id", "app_key", "state"]
