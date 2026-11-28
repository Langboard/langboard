"""Current-authority reads of verified resources through host-installed adapters."""

import json
import re
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import AppConnection, AppResourceBinding
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.AppBoardConsent import _context
from langboard_shared.domain.services.AppConnectionAuthentication import _identity_hash
from langboard_shared.domain.services.AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
    _current,
    current_policy,
    require_connection_access,
)
from langboard_shared.domain.services.factory.SecretReferenceService import SecretAuditSource
from langboard_shared.helpers import InfraHelper
from .DevtronRead import read_selected_deployments, selected_deployment_arguments
from .MetadataTransport import MetadataUnavailable, approved_instance


# Trusted server adapters, not executable code or HTTP descriptors from a package.
READERS = {("devtron-panel", "deployments.list"): (selected_deployment_arguments, read_selected_deployments)}


def panel_read_keys(permission):
    """Advertise trusted installed adapters, never package-provided HTTP descriptors."""
    if not {"panels.render", "resources.read"}.issubset(permission["granted_capabilities"]):
        return []
    return sorted(key for app_key, key in READERS if app_key == permission["key"])


def _uid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", value):
        raise ValueError("Invalid connection read identity")
    return InfraHelper.convert_id(value)


def _scope(db, actor, project_id, app_key, app_revision, binding_revision, connection_id, resource_id, access_revision):
    definition, project, binding = _context(
        db, actor, project_id, app_key, board_action=ProjectRoleAction.Read,
    )
    if (
        not definition.is_enabled or binding is None or binding.state != "enabled"
        or not {"panels.render", "resources.read"}.issubset(definition.declaration.get("capabilities", []))
        or not {"panels.render", "resources.read"}.issubset(binding.granted_capabilities)
        or not definition.declaration.get("panel")
    ):
        raise AppGovernanceDenied()
    if definition.edit_revision() != app_revision or binding.edit_revision() != binding_revision:
        raise AppGovernanceConflict()
    connection = _current(db, AppConnection, connection_id)
    if connection is None or connection.app_key != app_key or not connection.credential_reference:
        raise AppGovernanceDenied()
    connection = require_connection_access(db, actor, project, connection)
    resource = _current(db, AppResourceBinding, resource_id)
    identity = _identity_hash(db, connection, definition=definition)
    if (
        resource is None or resource.board_binding_id != binding.id or resource.connection_id != connection.id
        or not resource.is_selected or resource.access_state != "granted"
        or resource.resource_type not in definition.declaration.get("resource_types", [])
        or resource.identity_hash != identity
    ):
        raise AppGovernanceDenied()
    if resource.access_revision != access_revision:
        raise AppGovernanceConflict()
    return {
        "app_revision": definition.edit_revision(), "binding_revision": binding.edit_revision(),
        "connection_revision": connection.edit_revision(), "identity": identity,
        "policy_revision": current_policy(db, project.organization_id)["revision"],
        "access_revision": resource.access_revision, "resource_type": resource.resource_type,
        "external_id": resource.external_resource_id,
        "base": approved_instance(connection.instance_url), "uri": connection.credential_reference,
    }


def read_connection(service, actor, project_uid, app_key, app_revision, binding_revision, connection_uid, read_key, inputs):
    """No grant, resource health write, provider mutation or automatic retry."""
    if DbSession.has_active_transaction():
        raise RuntimeError("Connection reads must own their transaction")
    if not isinstance(read_key, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", read_key):
        raise ValueError("Invalid registered read key")
    if not isinstance(inputs, dict):
        raise ValueError("Connection read input must be an object")
    try:
        encoded = json.dumps(inputs, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Invalid connection read input") from None
    if len(encoded) > 16384:
        raise ValueError("Connection read input is too large")
    resource_id = _uid(inputs.get("resource_uid"))
    access_revision = inputs.get("access_revision")
    if type(access_revision) is not int or access_revision < 0:
        raise ValueError("A current resource revision is required")
    project_id, connection_id = _uid(project_uid), _uid(connection_uid)
    with DbSession.atomic() as db:
        before = _scope(db, actor, project_id, app_key, app_revision, binding_revision,
                        connection_id, resource_id, access_revision)
    reader = READERS.get((app_key, read_key))
    if reader is None:
        raise ValueError("Unsupported registered connection read")
    prepare, execute = reader
    arguments = prepare(before["resource_type"], before["external_id"],
                        {key: value for key, value in inputs.items() if key not in {"resource_uid", "access_revision"}})
    # Invalid adapter arguments are rejected before resolving the credential.
    secret_revision = service.secret_reference.get_metadata(actor, before["uri"])["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, before["uri"], source=SecretAuditSource("app_connection", connection_uid),
    ).get_secret_value()
    with DbSession.atomic() as db:
        dispatch = _scope(db, actor, project_id, app_key, app_revision, binding_revision,
                          connection_id, resource_id, access_revision)
        secret = service.secret_reference._find(actor, before["uri"], lock=True).metadata()
        if before != dispatch or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppGovernanceConflict()
    result = execute(before["base"], token, arguments)
    if not isinstance(result, dict):
        raise MetadataUnavailable()
    try:
        result_bytes = json.dumps(result, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise MetadataUnavailable() from None
    if len(result_bytes) > 120 * 1024 or json.dumps(token)[1:-1].encode() in result_bytes or before["uri"].encode() in result_bytes:
        raise MetadataUnavailable()
    with DbSession.atomic() as db:
        after = _scope(db, actor, project_id, app_key, app_revision, binding_revision,
                       connection_id, resource_id, access_revision)
        secret = service.secret_reference._find(actor, before["uri"], lock=True).metadata()
        if before != after or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppGovernanceConflict()
    return {"ok": True, "data": result}
