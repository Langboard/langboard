"""Verified Devtron accounts using the existing host connection and secret store."""

import json
import re
import httpx
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppConnection, AppResourceBinding
from langboard_shared.domain.services.AppBoardConsent import _context
from langboard_shared.domain.services.AppConnectionAuthentication import _identity_hash, _manage
from langboard_shared.domain.services.AppConnectionManagement import _definition
from langboard_shared.domain.services.AppGovernance import (
    AppGovernanceDenied,
    _actor,
    _authorize,
    _current,
    current_policy,
    lock_policy_mutation,
    require_connection_access,
    require_current_project_policy,
)
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict
from langboard_shared.domain.services.AppResourceAccess import revoke_connection_resources
from langboard_shared.domain.services.factory.SecretReferenceService import SecretAuditSource
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import AppSettingPublisher
from .DevtronRead import read_metadata, read_spec, selected_deployment_arguments
from .MetadataTransport import MetadataUnavailable, approved_instance


APP_KEY = "devtron-panel"


def _scope(db, actor, project_uid, app_revision, organization_id):
    actor = _actor(db, actor)
    _, board, _ = _context(db, actor, InfraHelper.convert_id(project_uid), APP_KEY, lock_binding=False)
    _definition(db, APP_KEY, app_revision)
    policy = require_current_project_policy(db, board)
    if organization_id is not None:
        _authorize(db, actor, organization_id)
        if board.organization_id != organization_id:
            raise AppGovernanceDenied()
    return actor, board, (board.organization_id, policy["revision"])


def _metadata(connection):
    return {
        "connection_uid": connection.get_uid(),
        "ownership": connection.ownership,
        "instance_url": connection.instance_url,
        "state": connection.state,
        "revision": connection.edit_revision(),
    }


def register_connection(
    service, actor, project_uid, app_revision, instance_url, credential_reference, organization_id=None,
    *, deployment_scope=None,
):
    """Verify before persistence, then recheck authority and the exact secret revision.

    Identical current accounts reuse their receipt under the policy mutation lock.
    This creates an external account, never converts an inbound app principal.
    """
    if DbSession.has_active_transaction():
        raise RuntimeError("Devtron registration must own its transaction")
    if not isinstance(credential_reference, str) or not re.fullmatch(
        r"secret://ref/[A-Za-z0-9]{1,11}", credential_reference
    ):
        raise ValueError("Use a canonical secret reference")
    if deployment_scope is None:
        operation, arguments = "applications.list", {"size": 1}
    else:
        if not isinstance(deployment_scope, dict) or deployment_scope.keys() - {
            "appId", "environmentId", "installedAppId", "helmAppId"
        }:
            raise ValueError("Invalid verification scope")
        operation, arguments = "deployments.list", {**deployment_scope, "limit": 1}
    read_spec(operation, arguments)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor, _, authority = _scope(db, actor, project_uid, app_revision, organization_id)
    base = approved_instance(instance_url)
    secret_revision = service.secret_reference.get_metadata(actor, credential_reference)["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, credential_reference, source=SecretAuditSource("api", "devtron_connection")
    ).get_secret_value()
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor, _, dispatch = _scope(db, actor, project_uid, app_revision, organization_id)
        approved_instance(base)
        secret = service.secret_reference._find(actor, credential_reference, lock=True).metadata()
        if dispatch != authority or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
    # Helm-only accounts can verify an explicit release without native-CD inventory
    # permission. Verification does not select or grant that resource on a board.
    # No transaction or policy lock is held across provider network I/O.
    read_metadata(base, token, operation, arguments)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor, board, after = _scope(db, actor, project_uid, app_revision, organization_id)
        approved_instance(base)
        secret = service.secret_reference._find(actor, credential_reference, lock=True).metadata()
        if after != authority or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
        ownership = "organization" if organization_id is not None else "personal"
        connection = db.exec(
            SqlBuilder.select.table(AppConnection).where(
                AppConnection.app_key == APP_KEY,
                AppConnection.owner_id == actor.id,
                AppConnection.organization_id == organization_id,
                AppConnection.ownership == ownership,
                AppConnection.instance_url == base,
                AppConnection.credential_reference == credential_reference,
                AppConnection.state == "connected",
            ).with_for_update()
        ).first()
        if connection is None:
            connection = AppConnection(
                app_key=APP_KEY,
                owner_id=actor.id,
                organization_id=organization_id,
                ownership=ownership,
                instance_url=base,
                credential_reference=credential_reference,
                state="connected",
            )
            db.insert(connection)
            db.after_commit(AppSettingPublisher.apps_changed)
        require_connection_access(db, actor, board, connection)
        service.secret_reference.audit_binding(
            actor, credential_reference, secret_revision,
            source=SecretAuditSource("app_connection", connection.get_uid()),
        )
        return _metadata(connection)


def _discovery_scope(service, db, actor, project_uid, app_revision, connection_uid):
    actor, board, authority = _scope(db, actor, project_uid, app_revision, None)
    definition = _definition(db, APP_KEY, app_revision)
    if "resources.read" not in definition.declaration.get("capabilities", []):
        raise AppGovernanceDenied()
    if not isinstance(connection_uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", connection_uid):
        raise ValueError("Invalid connection identity")
    connection = db.exec(SqlBuilder.select.table(AppConnection).where(
        AppConnection.id == InfraHelper.convert_id(connection_uid), AppConnection.app_key == APP_KEY,
    ).with_for_update()).first()
    if connection is None or not connection.credential_reference:
        raise AppGovernanceDenied()
    connection = require_connection_access(db, actor, board, connection)
    return actor, connection, authority


def discover_applications(service, actor, project_uid, app_revision, connection_uid, *, search="", offset=0, size=50):
    """Management discovery only; no resource selection, grant or raw provider return.

    Native CD inventory permission is needed only for this explicit discovery.
    Installed/external Helm registration remains independent of this endpoint.
    """
    if DbSession.has_active_transaction():
        raise RuntimeError("Devtron discovery must own its transaction")
    arguments = {"search": search, "offset": offset, "size": size}
    read_spec("applications.list", arguments)
    with DbSession.atomic() as db:
        actor, connection, authority = _discovery_scope(service, db, actor, project_uid, app_revision, connection_uid)
        revision, uri = connection.edit_revision(), connection.credential_reference
        base = approved_instance(connection.instance_url)
    secret_revision = service.secret_reference.get_metadata(actor, uri)["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, uri, source=SecretAuditSource("app_connection", connection_uid)
    ).get_secret_value()
    with DbSession.atomic() as db:
        _, current, dispatch = _discovery_scope(service, db, actor, project_uid, app_revision, connection_uid)
        approved_instance(current.instance_url)
        secret = service.secret_reference._find(actor, uri, lock=True).metadata()
        if dispatch != authority or current.edit_revision() != revision or current.credential_reference != uri or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
    result = read_metadata(base, token, "applications.list", arguments)
    resources = []
    for row in result["appContainers"]:
        app_id, name = row.get("appId"), row.get("appName")
        environments = row.get("environments")
        if type(app_id) is not int or not 1 <= app_id <= 1_000_000_000 or not isinstance(name, str) or not 1 <= len(name) <= 200:
            raise ValueError("Invalid application inventory")
        if environments is None:
            environments = []
        if not isinstance(environments, list) or len(environments) > 250:
            raise ValueError("Invalid environment inventory")
        for environment in environments:
            if not isinstance(environment, dict):
                raise ValueError("Invalid environment inventory")
            env_id, env_name = environment.get("environmentId"), environment.get("environmentName")
            if type(env_id) is not int or not 1 <= env_id <= 1_000_000_000 or not isinstance(env_name, str) or not 1 <= len(env_name) <= 200:
                raise ValueError("Invalid environment inventory")
            resources.append({"appId": app_id, "appName": name, "environmentId": env_id, "environmentName": env_name})
            if len(resources) > 250:
                raise ValueError("Application inventory page is too large")
    serialized = json.dumps(resources, allow_nan=False)
    if json.dumps(token)[1:-1] in serialized or uri in serialized:
        raise MetadataUnavailable()
    with DbSession.atomic() as db:
        _, current, after = _discovery_scope(service, db, actor, project_uid, app_revision, connection_uid)
        approved_instance(current.instance_url)
        secret = service.secret_reference._find(actor, uri, lock=True).metadata()
        if after != authority or current.edit_revision() != revision or current.credential_reference != uri or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
    return {"connection_uid": connection_uid, "connection_revision": revision, "resources": resources}


def _verification_scope(service, db, actor, project_uid, app_revision, binding_revision,
                        connection_uid, resource_uid, access_revision):
    lock_policy_mutation(db)
    actor = _actor(db, actor)
    # Match registered panel reads: actor -> definition -> board -> binding ->
    # connection -> resource. Do not take the board before the definition.
    definition, board, binding = _context(db, actor, InfraHelper.convert_id(project_uid), APP_KEY)
    if (not definition.is_enabled or binding is None or binding.state != "enabled"
            or "resources.read" not in definition.declaration.get("capabilities", [])
            or "resources.read" not in binding.granted_capabilities):
        raise AppGovernanceDenied()
    if definition.edit_revision() != app_revision or binding.edit_revision() != binding_revision:
        raise AppRegistryConflict()
    policy = require_current_project_policy(db, board)
    connection = _current(db, AppConnection, InfraHelper.convert_id(connection_uid))
    if connection is None or connection.app_key != APP_KEY or not connection.credential_reference:
        raise AppGovernanceDenied()
    connection = require_connection_access(db, actor, board, connection)
    _manage(db, actor, connection)
    resource = _current(db, AppResourceBinding, InfraHelper.convert_id(resource_uid))
    identity = _identity_hash(db, connection, definition=definition)
    if (resource is None or resource.board_binding_id != binding.id or resource.connection_id != connection.id
            or not resource.is_selected or resource.identity_hash != identity
            or resource.resource_type not in definition.declaration.get("resource_types", [])):
        raise AppGovernanceDenied()
    if resource.access_revision != access_revision:
        raise AppRegistryConflict()
    return actor, resource, {
        "authority": (board.organization_id, policy["revision"]), "connection_revision": connection.edit_revision(),
        "identity": identity, "base": approved_instance(connection.instance_url),
        "uri": connection.credential_reference, "resource_type": resource.resource_type,
        "external_id": resource.external_resource_id,
    }


def verify_selected_resource(service, actor, project_uid, app_revision, binding_revision,
                             connection_uid, resource_uid, access_revision, *, helm_app_id=None):
    """Probe an existing selection; never create or select a different resource.

    An empty authenticated deployment history can be valid. Provider rejection
    removes cached read access; transport/shape uncertainty cannot grant it.
    """
    if DbSession.has_active_transaction():
        raise RuntimeError("Resource verification must own its transaction")
    if (any(not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", uid)
            for uid in (project_uid, connection_uid, resource_uid))
            or type(access_revision) is not int or access_revision < 0):
        raise ValueError("A current selected resource revision is required")
    scope = (project_uid, app_revision, binding_revision, connection_uid, resource_uid, access_revision)
    with DbSession.atomic() as db:
        actor, _, before = _verification_scope(service, db, actor, *scope)
    filters = {} if helm_app_id is None else {"helmAppId": helm_app_id}
    arguments = selected_deployment_arguments(before["resource_type"], before["external_id"], filters)
    arguments["limit"] = 1
    secret_revision = service.secret_reference.get_metadata(actor, before["uri"])["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, before["uri"], source=SecretAuditSource("app_connection", connection_uid)
    ).get_secret_value()
    with DbSession.atomic() as db:
        _, _, dispatch = _verification_scope(service, db, actor, *scope)
        secret = service.secret_reference._find(actor, before["uri"], lock=True).metadata()
        if before != dispatch or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
    error = None
    access, health = "granted", "unknown"
    try:
        # The closed adapter checks the exact app/environment or Helm identity.
        # No DB transaction spans this request; provider bodies are discarded.
        read_metadata(before["base"], token, "deployments.list", arguments, timeout=8)
    except MetadataUnavailable as exc:
        access, health = ("denied" if exc.status_code in {401, 403, 404} else "unknown"), "unavailable"
        error = {"code": "upstream_error"}
        if exc.status_code is not None:
            error["status"] = exc.status_code
    except httpx.HTTPError:
        access, health = "unknown", "unavailable"
        error = {"code": "unavailable"}
    with DbSession.atomic() as db:
        _, resource, after = _verification_scope(service, db, actor, *scope)
        secret = service.secret_reference._find(actor, before["uri"], lock=True).metadata()
        if before != after or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
        resource.access_state, resource.health = access, health
        resource.access_revision += 1
        db.update(resource)
        db.after_commit(AppSettingPublisher.apps_changed)
        result = {"ok": error is None, "resource": {
            "resource_uid": resource.get_uid(), "access_state": access, "health": health,
            "access_revision": resource.access_revision, "binding_revision": binding_revision,
        }}
        if error is not None:
            result["error"] = error
        return result


def list_connections(actor, project_uid, *, organization_id=None, after=None, limit=25, connection_uid=None):
    """Owner-scoped metadata remains readable for withdrawal after policy disable."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid connection page")
    if after is not None and (not isinstance(after, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", after)):
        raise ValueError("Invalid connection cursor")
    if connection_uid is not None and (after is not None or not isinstance(connection_uid, str)
                                       or not re.fullmatch(r"[A-Za-z0-9]{11}", connection_uid)):
        raise ValueError("Invalid exact connection lookup")
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        definition, board, binding = _context(db, actor, InfraHelper.convert_id(project_uid), APP_KEY,
                                               for_update=False, lock_binding=False)
        scopes = [None]
        if board.organization_id is not None:
            try:
                _authorize(db, actor, board.organization_id)
            except AppGovernanceDenied:
                pass
            else:
                scopes.append(InfraHelper.convert_uid(board.organization_id))
        if organization_id is not None:
            _authorize(db, actor, organization_id)
            if board.organization_id != organization_id:
                raise AppGovernanceDenied()
        statement = SqlBuilder.select.table(AppConnection).where(
            AppConnection.app_key == APP_KEY, AppConnection.credential_reference.is_not(None),
            AppConnection.instance_url != "", AppConnection.organization_id == organization_id,
            AppConnection.ownership == ("organization" if organization_id is not None else "personal"),
        )
        if organization_id is None:
            statement = statement.where(AppConnection.owner_id == actor.id)
        if after is not None:
            statement = statement.where(AppConnection.id > InfraHelper.convert_id(after))
        if connection_uid is not None:
            statement = statement.where(AppConnection.id == InfraHelper.convert_id(connection_uid))
        rows = db.exec(statement.order_by(AppConnection.id).limit(limit + 1)).all()
        return {
            "app_revision": definition.edit_revision(),
            "available": definition.is_enabled and current_policy(db, board.organization_id)["effective_mode"] != "disabled",
            "ownership_scopes": scopes,
            "binding": None if binding is None else {
                "uid": binding.get_uid(), "revision": binding.edit_revision(), "state": binding.state,
                "granted_capabilities": sorted(set(binding.granted_capabilities)
                    & set(definition.declaration.get("capabilities", []))),
            },
            "items": [_metadata(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1].get_uid() if len(rows) > limit else None,
        }


def disconnect_connection(actor, project_uid, connection_uid, expected_revision):
    """Explicit host revocation; external credentials and resource rows are retained."""
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor = _actor(db, actor)
        _, board, _ = _context(db, actor, InfraHelper.convert_id(project_uid), APP_KEY)
        connection = _current(db, AppConnection, InfraHelper.convert_id(connection_uid))
        if (connection is None or connection.app_key != APP_KEY or not connection.credential_reference
                or not connection.instance_url or connection.ownership not in {"personal", "organization"}
                or (connection.ownership == "personal" and connection.organization_id is not None)):
            raise AppGovernanceDenied()
        _manage(db, actor, connection)
        if connection.ownership == "organization" and board.organization_id != connection.organization_id:
            raise AppGovernanceDenied()
        if connection.edit_revision() != expected_revision:
            raise AppRegistryConflict()
        if connection.state != "disconnected":
            connection.state = "disconnected"
            db.update(connection)
            revoke_connection_resources(db, connection.id)
            db.after_commit(AppSettingPublisher.apps_changed)
        return _metadata(_current(db, AppConnection, connection.id))
