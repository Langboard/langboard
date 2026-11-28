"""Host-owned App declarations; discovery never installs code or grants access."""

from dataclasses import dataclass
from types import MappingProxyType
from langboard_sdk import WorkflowRequirements
from ..models.ProjectRole import ProjectRoleAction


@dataclass(frozen=True)
class AppSignalPolicy:
    """Host-installed adapter declaration; never a caller-supplied access grant."""

    event_types: tuple[str, ...]
    resource_types: tuple[str, ...]
    required_capabilities: tuple[str, ...] = ("signals.read",)
    requires_empty_commit: bool = False
    requires_commit: bool = False
    time_basis: str | None = "provider_occurrence"
    evidence_kind: str = "check"
    outcome_states: tuple[tuple[str, str], ...] = (
        ("success", "passed"), ("failure", "failed"), ("timed_out", "failed"),
    )
    event_time_bases: tuple[tuple[str, str], ...] = ()

    def basis_for_event(self, event_type: str | None) -> str | None:
        if self.event_time_bases:
            return dict(self.event_time_bases).get(event_type)
        return self.time_basis

    def kind_for_basis(self, basis: str | None) -> str | None:
        if self.evidence_kind in ("external_event", "external_observation"):
            return {"observation": "external_observation", "provider_occurrence": "external_event"}.get(basis)
        return self.evidence_kind


@dataclass(frozen=True)
class AppManifest:
    key: str
    name: str
    resource_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    workflow_requirements: WorkflowRequirements | None = None
    # Current host role actions, not provider OAuth scopes or granted permissions.
    read_permission: ProjectRoleAction = ProjectRoleAction.Read
    configure_permission: ProjectRoleAction = ProjectRoleAction.Update
    signal_schema_version: int = 1
    signal_policy: AppSignalPolicy | None = None
    version: str | None = None
    description: str = ""
    panel: dict | None = None
    # Implementation availability only; current configuration authority remains separate.
    connection_setup_available: bool = False

    def catalog_fields(self) -> dict:
        requirements = self.workflow_requirements
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "panel": self.panel,
            "resource_types": list(self.resource_types),
            "capabilities": list(self.capabilities),
            "permissions": {"read": self.read_permission.value, "configure": self.configure_permission.value},
            "workflow_requirements": None
            if requirements is None
            else {
                "required": list(requirements.required),
                "optional": list(requirements.optional),
            },
            "signal_schema": {
                "version": self.signal_schema_version,
                "required": ["event_id", "event_type", "occurred_at", "connection_uid", "resource_uid"],
            },
            "connection_setup_available": self.connection_setup_available,
        }


APP_MANIFESTS = MappingProxyType(
    {
        "github": AppManifest(
            "github",
            "GitHub",
            ("repository",),
            ("resources.read", "signals.read", "workflow.transition"),
            WorkflowRequirements(("active", "review", "closed"), ("ready",)),
            signal_policy=AppSignalPolicy(("check.completed",), ("repository",), requires_commit=True),
            connection_setup_available=True,
        ),
        "glitchtip": AppManifest(
            "glitchtip",
            "GlitchTip",
            ("organization", "project"),
            ("resources.read", "signals.read", "workflow.transition"),
            WorkflowRequirements(("active", "review", "closed")),
            signal_policy=AppSignalPolicy(
                ("issue.status_observed",), ("project",), ("signals.read", "resources.read"),
                requires_empty_commit=True, time_basis="observation", evidence_kind="issue_observation",
                outcome_states=(("unresolved", "failed"), ("resolved", "resolved"), ("ignored", "ignored")),
            ),
            connection_setup_available=True,
        ),
        "dokploy": AppManifest(
            "dokploy",
            "Dokploy",
            ("project", "environment", "application", "compose"),
            ("resources.read", "signals.read", "deployments.read"),
            signal_policy=AppSignalPolicy(
                ("deployment.queued", "deployment.started", "deployment.succeeded", "deployment.failed", "deployment.cancelled"),
                ("application", "compose"), ("signals.read", "deployments.read"), requires_empty_commit=True,
                evidence_kind="deployment",
                outcome_states=(
                    ("success", "passed"), ("failure", "failed"), ("timed_out", "failed"),
                    ("queued", "queued"), ("running", "running"), ("cancelled", "cancelled"),
                ),
            ),
            connection_setup_available=True,
        ),
    }
)
