"""Verified Devtron accounts using the existing host connection and secret store."""

import re
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppConnection
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.AppConnectionManagement import _definition
from langboard_shared.domain.services.AppGovernance import (
    AppGovernanceDenied,
    _actor,
    _authorize,
    lock_policy_mutation,
    require_connection_access,
    require_current_project_policy,
)
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict
from langboard_shared.domain.services.factory.SecretReferenceService import SecretAuditSource
from langboard_shared.publishers import AppSettingPublisher
from langboard_shared.helpers import InfraHelper
from .DevtronRead import read_metadata, read_spec
from .MetadataTransport import approved_instance


APP_KEY = "devtron-panel"


def _scope(service, db, actor, project_uid, app_revision, organization_id):
    actor = _actor(db, actor)
    board = service.workflow_stage._authorized_app_board(actor, project_uid, ProjectRoleAction.Update, lock=True)
    if board is None:
        raise AppGovernanceDenied()
    _definition(db, APP_KEY, app_revision)
    require_current_project_policy(db, board)
    if organization_id is not None:
        _authorize(db, actor, organization_id)
        if board.organization_id != organization_id:
            raise AppGovernanceDenied()
    return actor, board


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
        actor, _ = _scope(service, db, actor, project_uid, app_revision, organization_id)
    base = approved_instance(instance_url)
    secret_revision = service.secret_reference.get_metadata(actor, credential_reference)["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, credential_reference, source=SecretAuditSource("api", "devtron_connection")
    ).get_secret_value()
    # Helm-only accounts can verify an explicit release without native-CD inventory
    # permission. Verification does not select or grant that resource on a board.
    # No transaction or policy lock is held across provider network I/O.
    read_metadata(base, token, operation, arguments)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor, board = _scope(service, db, actor, project_uid, app_revision, organization_id)
        approved_instance(base)
        secret = service.secret_reference._find(actor, credential_reference, lock=True).metadata()
        if secret["state"] != "active" or secret["revision"] != secret_revision:
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
    actor, board = _scope(service, db, actor, project_uid, app_revision, None)
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
    return actor, connection


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
        actor, connection = _discovery_scope(service, db, actor, project_uid, app_revision, connection_uid)
        revision, uri = connection.edit_revision(), connection.credential_reference
        base = approved_instance(connection.instance_url)
    secret_revision = service.secret_reference.get_metadata(actor, uri)["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, uri, source=SecretAuditSource("app_connection", connection_uid)
    ).get_secret_value()
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
    with DbSession.atomic() as db:
        _, current = _discovery_scope(service, db, actor, project_uid, app_revision, connection_uid)
        approved_instance(current.instance_url)
        secret = service.secret_reference._find(actor, uri, lock=True).metadata()
        if current.edit_revision() != revision or current.credential_reference != uri or secret["state"] != "active" or secret["revision"] != secret_revision:
            raise AppRegistryConflict()
    return {"connection_uid": connection_uid, "connection_revision": revision, "resources": resources}
