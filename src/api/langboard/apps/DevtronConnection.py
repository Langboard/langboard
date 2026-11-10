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
from .DevtronRead import read_metadata
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
    service, actor, project_uid, app_revision, instance_url, credential_reference, organization_id=None
):
    """Verify before persistence, then recheck authority and the exact secret revision.

    Identical current accounts reuse their receipt under the policy mutation lock.
    This creates an external account, never converts an inbound app principal.
    """
    if not isinstance(credential_reference, str) or not re.fullmatch(
        r"secret://ref/[A-Za-z0-9]{1,11}", credential_reference
    ):
        raise ValueError("Use a canonical secret reference")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor, _ = _scope(service, db, actor, project_uid, app_revision, organization_id)
    base = approved_instance(instance_url)
    secret_revision = service.secret_reference.get_metadata(actor, credential_reference)["revision"]
    token = service.secret_reference.resolve_for_runtime(
        actor, credential_reference, source=SecretAuditSource("api", "devtron_connection")
    ).get_secret_value()
    # A filtered discovery read verifies this token against the configured instance.
    # No transaction or policy lock is held across provider network I/O.
    read_metadata(base, token, "applications.list", {"size": 1})
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
